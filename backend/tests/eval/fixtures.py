"""Labelled receipt fixtures for the OCR eval harness.

Receipts are **generated**, not photographed, because generation is the only way
to get ground truth: for every image we know exactly what the merchant, date and
total are, so field accuracy is measurable rather than eyeballed.

**Be honest about what this measures.** A rendered receipt is cleaner than a
crumpled thermal one shot under a kitchen light, so the number this produces is
an *upper bound* on real-world accuracy. To keep it from being a fantasy, every
fixture is degraded on the way out -- rotation, perspective, blur, noise, and
uneven lighting -- so the preprocessing pipeline is genuinely exercised rather
than handed a clean scan. The eval report states this alongside the score.

Deterministic under a fixed seed, so the baseline moves only when the pipeline
does.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO
from typing import Any

SEED = 20260805

MERCHANTS = (
    "RELIANCE FRESH",
    "BIG BAZAAR",
    "CAFE COFFEE DAY",
    "APOLLO PHARMACY",
    "MORE SUPERMARKET",
    "DMART",
    "SPENCERS RETAIL",
    "NATURALS ICE CREAM",
    "BLUE TOKAI COFFEE",
    "HALDIRAMS",
)

ITEMS = (
    ("Milk 1L", 62.00),
    ("Brown Bread", 45.00),
    ("Basmati Rice 5kg", 480.00),
    ("Amul Butter", 58.00),
    ("Eggs 12pc", 84.00),
    ("Tomatoes 1kg", 40.00),
    ("Green Tea", 220.00),
    ("Olive Oil 500ml", 399.00),
    ("Toothpaste", 95.00),
    ("Dish Soap", 65.00),
)


@dataclass(slots=True)
class Fixture:
    """One receipt image plus the truth it was rendered from."""

    name: str
    image: bytes
    truth: dict[str, Any] = field(default_factory=dict)
    degradation: str = "none"


def _render(
    merchant: str,
    date_text: str,
    lines: list[tuple[str, float]],
    subtotal: float,
    tax: float,
    total: float,
) -> Any:
    """Draw a receipt on a light background at a plausible resolution."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 640, 200 + len(lines) * 34 + 200
    image = Image.new("RGB", (width, height), (250, 249, 246))
    draw = ImageDraw.Draw(image)

    def font(size: int) -> Any:
        # DejaVu ships with Pillow's test assets on Debian; fall back to the
        # bitmap default if it is absent so the harness still runs.
        for path in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    title, body = font(30), font(21)
    y = 40

    draw.text((40, y), merchant, fill=(15, 15, 15), font=title)
    y += 52
    draw.text((40, y), f"Date {date_text}", fill=(30, 30, 30), font=body)
    y += 42
    draw.line((30, y, width - 30, y), fill=(90, 90, 90), width=2)
    y += 22

    for description, price in lines:
        draw.text((40, y), description, fill=(30, 30, 30), font=body)
        draw.text((width - 190, y), f"{price:.2f}", fill=(30, 30, 30), font=body)
        y += 34

    y += 12
    draw.line((30, y, width - 30, y), fill=(90, 90, 90), width=2)
    y += 22

    for label, value in (("SUBTOTAL", subtotal), ("CGST 9%", tax)):
        draw.text((40, y), label, fill=(30, 30, 30), font=body)
        draw.text((width - 190, y), f"{value:.2f}", fill=(30, 30, 30), font=body)
        y += 34

    y += 8
    draw.text((40, y), "TOTAL", fill=(0, 0, 0), font=title)
    draw.text((width - 210, y), f"{total:.2f}", fill=(0, 0, 0), font=title)

    return image


def _degrade(image: Any, kind: str, rng: random.Random) -> Any:
    """Simulate a phone photo.

    Each mode targets one stage of the preprocessing pipeline, so a regression
    in deskewing or thresholding shows up as a drop in the score rather than
    passing unnoticed on clean input.
    """
    from PIL import Image, ImageEnhance, ImageFilter

    if kind == "clean":
        return image

    if kind == "rotated":
        return image.rotate(rng.uniform(-6, 6), expand=True, fillcolor=(250, 249, 246))

    if kind == "blurred":
        return image.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.6, 1.2)))

    if kind == "low_contrast":
        return ImageEnhance.Contrast(image).enhance(rng.uniform(0.45, 0.65))

    if kind == "noisy":
        import numpy as np

        array = np.array(image).astype(np.int16)
        noise = np.random.default_rng(rng.randint(0, 10_000)).normal(0, 16, array.shape)
        return Image.fromarray(np.clip(array + noise, 0, 255).astype(np.uint8))

    if kind == "uneven_light":
        # A gradient across the page: the case a global threshold cannot
        # handle and adaptive thresholding exists for.
        import numpy as np

        array = np.array(image).astype(np.float32)
        gradient = np.linspace(0.55, 1.15, array.shape[1])[None, :, None]
        return Image.fromarray(np.clip(array * gradient, 0, 255).astype(np.uint8))

    if kind == "perspective":
        # Shot at an angle. Exercises the four-point transform.
        width, height = image.size
        shift = int(width * rng.uniform(0.04, 0.09))
        coeffs = _perspective_coeffs(
            [(0, 0), (width, 0), (width, height), (0, height)],
            [(shift, 0), (width - shift // 2, shift // 2), (width, height), (0, height - shift)],
        )
        return image.transform(
            (width, height), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(250, 249, 246)
        )

    return image


def _perspective_coeffs(
    source: list[tuple[int, int]], target: list[tuple[int, int]]
) -> list[float]:
    """Solve the 8 coefficients PIL's PERSPECTIVE transform expects.

    Plain ndarray and least squares -- np.matrix has surprising broadcasting
    rules and is deprecated.
    """
    import numpy as np

    rows = []
    for (sx, sy), (tx, ty) in zip(source, target, strict=True):
        rows.append([tx, ty, 1, 0, 0, 0, -sx * tx, -sx * ty])
        rows.append([0, 0, 0, tx, ty, 1, -sy * tx, -sy * ty])

    a = np.array(rows, dtype=np.float64)
    b = np.array(source, dtype=np.float64).reshape(8)
    coeffs, *_ = np.linalg.lstsq(a, b, rcond=None)
    return [float(c) for c in coeffs]


DEGRADATIONS = (
    "clean",
    "rotated",
    "blurred",
    "low_contrast",
    "noisy",
    "uneven_light",
    "perspective",
)


def build_fixtures(count: int = 20, seed: int = SEED) -> list[Fixture]:
    """Generate labelled receipts across the degradation modes."""
    # Not cryptographic: a fixed seed is the point, so the baseline moves
    # only when the pipeline does.
    rng = random.Random(seed)
    fixtures: list[Fixture] = []

    for index in range(count):
        merchant = MERCHANTS[index % len(MERCHANTS)]
        day = rng.randint(1, 28)
        month = rng.randint(1, 12)
        date_text = f"{day:02d}/{month:02d}/2026"

        chosen = rng.sample(ITEMS, rng.randint(2, 5))
        subtotal = round(sum(price for _, price in chosen), 2)
        tax = round(subtotal * 0.09, 2)
        total = round(subtotal + tax, 2)

        image = _render(merchant, date_text, list(chosen), subtotal, tax, total)
        degradation = DEGRADATIONS[index % len(DEGRADATIONS)]
        image = _degrade(image, degradation, rng)

        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG", quality=88)

        fixtures.append(
            Fixture(
                name=f"{index:02d}-{merchant.lower().replace(' ', '-')}-{degradation}",
                image=buffer.getvalue(),
                truth={
                    "merchant": merchant,
                    "date": f"2026-{month:02d}-{day:02d}",
                    "total": Decimal(f"{total:.2f}"),
                    "subtotal": Decimal(f"{subtotal:.2f}"),
                    "tax": Decimal(f"{tax:.2f}"),
                },
                degradation=degradation,
            )
        )

    return fixtures
