"""OpenCV preprocessing.

**This is where the accuracy comes from.** Tesseract on a raw phone photo of a
thermal receipt reads at roughly 60--70%; most of the gap is not the OCR engine
but the image handed to it -- shot at an angle, unevenly lit, curled, and
photographed against a dark table.

Each stage below targets one of those failure modes, in the order that matters:
find the receipt, flatten it, straighten it, even out the lighting, then
binarise. Running them out of order is worse than not running them -- deskewing
before perspective correction, for instance, measures the angle of a trapezoid.

Imported only by the worker. `cv2` and `numpy` are absent from the API image.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# Downscale anything larger before processing. A 12 MP phone photo costs
# several hundred MB through the pipeline and gains nothing: Tesseract wants
# roughly 300 DPI on the text, which this comfortably exceeds.
MAX_EDGE = 1600

# A receipt occupying less than this share of the frame is probably a false
# contour -- a tile edge, a table seam -- so the crop is not trusted.
MIN_RECEIPT_AREA_RATIO = 0.20


@dataclass(slots=True)
class PreprocessReport:
    """What the pipeline did, so a bad extraction can be explained."""

    resized: bool = False
    perspective_corrected: bool = False
    deskewed_degrees: float = 0.0
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "resized": self.resized,
            "perspective_corrected": self.perspective_corrected,
            "deskewed_degrees": round(self.deskewed_degrees, 2),
            "width": self.width,
            "height": self.height,
        }


def preprocess(image_bytes: bytes) -> tuple[bytes, PreprocessReport]:
    """Return a binarised, deskewed PNG ready for OCR."""
    import cv2
    import numpy as np

    image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Could not decode the image")

    report = PreprocessReport()

    image, report.resized = _downscale(image)
    image, report.perspective_corrected = _correct_perspective(image)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray, report.deskewed_degrees = _deskew(gray)
    gray = _denoise(gray)
    binary = _binarise(gray)

    report.height, report.width = binary.shape[:2]

    ok, encoded = cv2.imencode(".png", binary)
    if not ok:
        raise ValueError("Could not encode the processed image")
    return encoded.tobytes(), report


def _downscale(image: Any) -> tuple[Any, bool]:
    import cv2

    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_EDGE:
        return image, False

    scale = MAX_EDGE / longest
    # INTER_AREA is the correct filter for shrinking; the default bilinear
    # aliases fine text into noise.
    resized = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return resized, True


def _correct_perspective(image: Any) -> tuple[Any, bool]:
    """Flatten a receipt photographed at an angle.

    Finds the largest four-sided contour and warps it to a rectangle. Bounded
    by MIN_RECEIPT_AREA_RATIO: if the best candidate is small, it is more likely
    a tile edge than the receipt, and warping to it would destroy the image. In
    that case the original is returned unchanged -- a slightly skewed receipt
    still reads; a receipt warped to the wrong quadrilateral does not.
    """
    import cv2
    import numpy as np

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # Close small gaps so a broken receipt edge still forms one contour.
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return image, False

    frame_area = image.shape[0] * image.shape[1]
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        area = cv2.contourArea(contour)
        if area < frame_area * MIN_RECEIPT_AREA_RATIO:
            break

        approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
        if len(approx) == 4:
            return _four_point_transform(image, approx.reshape(4, 2)), True

    return image, False


def _four_point_transform(image: Any, points: Any) -> Any:
    import cv2
    import numpy as np

    ordered = _order_corners(points)
    (top_left, top_right, bottom_right, bottom_left) = ordered

    width = int(
        max(np.linalg.norm(bottom_right - bottom_left), np.linalg.norm(top_right - top_left))
    )
    height = int(
        max(np.linalg.norm(top_right - bottom_right), np.linalg.norm(top_left - bottom_left))
    )
    if width < 50 or height < 50:
        return image

    destination = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32"
    )
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    return cv2.warpPerspective(image, matrix, (width, height))


def _order_corners(points: Any) -> Any:
    """Order corners top-left, top-right, bottom-right, bottom-left.

    By coordinate sums and differences rather than angles: the sum is smallest
    at the top-left and largest at the bottom-right, and the difference
    separates the other two. Robust regardless of which corner the contour
    happened to start from.
    """
    import numpy as np

    ordered = np.zeros((4, 2), dtype="float32")
    total = points.sum(axis=1)
    ordered[0] = points[np.argmin(total)]
    ordered[2] = points[np.argmax(total)]

    diff = np.diff(points, axis=1)
    ordered[1] = points[np.argmin(diff)]
    ordered[3] = points[np.argmax(diff)]
    return ordered


def _deskew(gray: Any) -> tuple[Any, float]:
    """Rotate text back to horizontal.

    Measures the minimum-area rectangle around the dark pixels. Only small
    angles are corrected: a large measured angle usually means the detector
    locked onto something other than the text block, and rotating by it would
    make things worse.
    """
    import cv2
    import numpy as np

    inverted = cv2.bitwise_not(gray)
    _, threshold = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    coords = np.column_stack(np.where(threshold > 0))
    if len(coords) < 100:
        return gray, 0.0

    angle = cv2.minAreaRect(coords.astype(np.float32))[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.5 or abs(angle) > 20:
        return gray, 0.0

    height, width = gray.shape
    matrix = cv2.getRotationMatrix2D((width // 2, height // 2), angle, 1.0)
    rotated = cv2.warpAffine(
        gray,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotated, float(angle)


def _denoise(gray: Any) -> Any:
    """Remove sensor and thermal-paper speckle.

    A bilateral filter rather than a Gaussian blur: it smooths flat areas while
    preserving edges, and on text the edges are the entire signal.
    """
    import cv2

    return cv2.bilateralFilter(gray, d=7, sigmaColor=50, sigmaSpace=50)


def _binarise(gray: Any) -> Any:
    """Convert to black and white under uneven lighting.

    Adaptive rather than global thresholding: a phone photo is nearly always
    brighter on one side, and a single global cutoff blows out one half of the
    receipt while filling the other with noise. CLAHE first, so the local
    contrast the threshold depends on actually exists.
    """
    import cv2

    equalised = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    return cv2.adaptiveThreshold(
        equalised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=31,
        C=10,
    )
