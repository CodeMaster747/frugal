"""The seeded product catalogue.

Reference data, not a scrape. Continuously scraping Amazon or Flipkart for the
advisor's prices was rejected in planning and the reasons have not changed: it
violates their terms, gets an IP banned within hours, and would make the
flagship feature's reliability a function of someone else's bot detection.

So the advisor ships against a hand-built catalogue of ~130 real products at
realistic Indian market prices, behind a `PriceProvider` port. A user can always
enter a price manually, which is the escape hatch that makes the catalogue's
coverage a convenience rather than a constraint. A real provider adapter is a
configuration change (ADR-004).

Prices are indicative and dated; they exist to make the *advice* realistic, not
to be a shopping comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CatalogItem:
    brand: str
    name: str
    category: str
    price: Decimal
    seller: str

    @property
    def external_id(self) -> str:
        """Stable id derived from the name, so it survives a reordering."""
        slug = "".join(c if c.isalnum() else "-" for c in self.name.lower())
        return f"seed:{self.brand.lower().replace(' ', '-')}:{slug.strip('-')[:60]}"

    @property
    def full_name(self) -> str:
        return f"{self.brand} {self.name}" if self.brand != "Generic" else self.name


def _item(brand: str, name: str, category: str, price: int, seller: str) -> CatalogItem:
    return CatalogItem(brand, name, category, Decimal(price), seller)


#: Categories chosen for the decisions people actually agonise over: a laptop, a
#: phone, a holiday, a two-wheeler. A catalogue of 50,000 SKUs would not make the
#: advice better -- the advice is about the user's finances, not the product.
CATALOG: tuple[CatalogItem, ...] = (
    # --- laptops ---
    _item("Apple", "MacBook Air M3 13-inch 8GB/256GB", "laptop", 114900, "Croma"),
    _item("Apple", "MacBook Air M3 13-inch 16GB/512GB", "laptop", 149900, "Amazon"),
    _item("Apple", "MacBook Air M2 13-inch 8GB/256GB", "laptop", 89900, "Flipkart"),
    _item("Apple", "MacBook Pro M4 14-inch 16GB/512GB", "laptop", 199900, "Croma"),
    _item("Dell", "XPS 13 9340 16GB/512GB", "laptop", 139990, "Amazon"),
    _item("Dell", "Inspiron 15 3520 16GB/512GB", "laptop", 54990, "Flipkart"),
    _item("Dell", "Latitude 3450 8GB/512GB", "laptop", 68990, "Vijay Sales"),
    _item("HP", "Pavilion 14 16GB/512GB", "laptop", 62999, "Reliance Digital"),
    _item("HP", "Spectre x360 14 16GB/1TB", "laptop", 154999, "Amazon"),
    _item("HP", "Victus 15 16GB/512GB RTX 3050", "laptop", 71999, "Flipkart"),
    _item("Lenovo", "IdeaPad Slim 5 16GB/512GB", "laptop", 58990, "Amazon"),
    _item("Lenovo", "ThinkPad E14 Gen 5 16GB/512GB", "laptop", 74990, "Croma"),
    _item("Lenovo", "Legion 5 Pro 16GB/1TB RTX 4060", "laptop", 129990, "Flipkart"),
    _item("ASUS", "Vivobook 15 16GB/512GB", "laptop", 47990, "Amazon"),
    _item("ASUS", "Zenbook 14 OLED 16GB/1TB", "laptop", 94990, "Croma"),
    _item("ASUS", "ROG Strix G16 16GB/1TB", "laptop", 144990, "Flipkart"),
    _item("Acer", "Aspire 7 16GB/512GB", "laptop", 52999, "Reliance Digital"),
    _item("MSI", "Thin 15 16GB/512GB", "laptop", 59990, "Amazon"),
    # --- smartphones ---
    _item("Apple", "iPhone 16 128GB", "smartphone", 79900, "Croma"),
    _item("Apple", "iPhone 16 Pro 256GB", "smartphone", 129900, "Amazon"),
    _item("Apple", "iPhone 15 128GB", "smartphone", 69900, "Flipkart"),
    _item("Samsung", "Galaxy S24 256GB", "smartphone", 74999, "Amazon"),
    _item("Samsung", "Galaxy S24 Ultra 512GB", "smartphone", 139999, "Croma"),
    _item("Samsung", "Galaxy A55 8GB/256GB", "smartphone", 39999, "Flipkart"),
    _item("Samsung", "Galaxy M35 6GB/128GB", "smartphone", 19999, "Amazon"),
    _item("OnePlus", "12 12GB/256GB", "smartphone", 64999, "Amazon"),
    _item("OnePlus", "Nord CE4 8GB/128GB", "smartphone", 24999, "Flipkart"),
    _item("Google", "Pixel 8a 128GB", "smartphone", 52999, "Flipkart"),
    _item("Google", "Pixel 9 Pro 256GB", "smartphone", 119999, "Croma"),
    _item("Xiaomi", "14 Ultra 16GB/512GB", "smartphone", 99999, "Amazon"),
    _item("Xiaomi", "Redmi Note 13 Pro 8GB/256GB", "smartphone", 25999, "Flipkart"),
    _item("Realme", "12 Pro Plus 8GB/256GB", "smartphone", 29999, "Flipkart"),
    _item("Vivo", "V40 Pro 8GB/256GB", "smartphone", 49999, "Reliance Digital"),
    _item("Nothing", "Phone 2a 8GB/128GB", "smartphone", 23999, "Flipkart"),
    # --- televisions ---
    _item("Sony", "Bravia 55-inch 4K OLED", "television", 189900, "Croma"),
    _item("Sony", "Bravia 43-inch 4K LED", "television", 54990, "Reliance Digital"),
    _item("Samsung", "Crystal 55-inch 4K UHD", "television", 49990, "Amazon"),
    _item("Samsung", "Neo QLED 65-inch 4K", "television", 164990, "Croma"),
    _item("LG", "OLED evo C4 55-inch", "television", 149990, "Vijay Sales"),
    _item("LG", "UR75 50-inch 4K", "television", 44990, "Flipkart"),
    _item("TCL", "C755 55-inch QD-Mini LED", "television", 79990, "Amazon"),
    _item("Xiaomi", "X Pro 55-inch 4K", "television", 39999, "Flipkart"),
    _item("OnePlus", "Q2 Pro 65-inch QLED", "television", 89999, "Amazon"),
    _item("Hisense", "E7K 55-inch QLED", "television", 42999, "Flipkart"),
    # --- audio ---
    _item("Apple", "AirPods Pro 2", "audio", 24900, "Croma"),
    _item("Apple", "AirPods Max", "audio", 59900, "Amazon"),
    _item("Sony", "WH-1000XM5", "audio", 29990, "Amazon"),
    _item("Sony", "WF-1000XM5", "audio", 24990, "Croma"),
    _item("Bose", "QuietComfort Ultra", "audio", 34900, "Amazon"),
    _item("Sennheiser", "Momentum 4", "audio", 27990, "Flipkart"),
    _item("JBL", "Tour One M2", "audio", 19999, "Amazon"),
    _item("boAt", "Rockerz 550", "audio", 2499, "Flipkart"),
    _item("Nothing", "Ear (a)", "audio", 7999, "Flipkart"),
    _item("Marshall", "Stanmore III", "audio", 34999, "Amazon"),
    # --- appliances ---
    _item("LG", "8kg Front Load Washing Machine", "appliance", 42990, "Croma"),
    _item("Samsung", "7kg Front Load Washing Machine", "appliance", 34990, "Reliance Digital"),
    _item("IFB", "6.5kg Front Load Washing Machine", "appliance", 29990, "Vijay Sales"),
    _item("LG", "260L Double Door Refrigerator", "appliance", 32990, "Croma"),
    _item("Samsung", "324L Double Door Refrigerator", "appliance", 38990, "Amazon"),
    _item("Whirlpool", "192L Single Door Refrigerator", "appliance", 16990, "Flipkart"),
    _item("Voltas", "1.5 Ton 5-Star Inverter AC", "appliance", 42990, "Croma"),
    _item("Daikin", "1.5 Ton 5-Star Inverter AC", "appliance", 49990, "Reliance Digital"),
    _item("Blue Star", "1 Ton 3-Star Inverter AC", "appliance", 31990, "Amazon"),
    _item("Bosch", "12-Place Dishwasher", "appliance", 44990, "Croma"),
    _item("Philips", "Air Fryer XL", "appliance", 12995, "Amazon"),
    _item("Dyson", "V12 Detect Slim", "appliance", 47900, "Croma"),
    # --- furniture ---
    _item("Wakefit", "Orthopaedic Memory Foam Queen Mattress", "furniture", 18999, "Amazon"),
    _item("Wakefit", "Study Table with Storage", "furniture", 8999, "Flipkart"),
    _item("Urban Ladder", "3-Seater Fabric Sofa", "furniture", 44999, "Amazon"),
    _item("Urban Ladder", "Solid Wood Queen Bed", "furniture", 38999, "Amazon"),
    _item("Pepperfry", "Ergonomic Office Chair", "furniture", 12999, "Flipkart"),
    _item("IKEA", "MARKUS Office Chair", "furniture", 17990, "Amazon"),
    _item("IKEA", "BILLY Bookcase", "furniture", 6990, "Amazon"),
    _item("Godrej", "4-Door Steel Wardrobe", "furniture", 27999, "Flipkart"),
    _item("Duroflex", "Back Magic Queen Mattress", "furniture", 21999, "Amazon"),
    _item("Featherlite", "Optima Task Chair", "furniture", 9499, "Flipkart"),
    # --- fitness ---
    _item("Cultsport", "Treadmill T400", "fitness", 34999, "Amazon"),
    _item("Cultsport", "Exercise Bike C40", "fitness", 18999, "Flipkart"),
    _item("Decathlon", "Domyos Adjustable Dumbbells 20kg", "fitness", 7999, "Amazon"),
    _item("Boldfit", "Home Gym Kit 40kg", "fitness", 5499, "Flipkart"),
    _item("Garmin", "Forerunner 265", "fitness", 49990, "Amazon"),
    _item("Garmin", "Venu 3", "fitness", 44990, "Croma"),
    _item("Apple", "Watch Series 10 46mm", "fitness", 46900, "Croma"),
    _item("Samsung", "Galaxy Watch 7 44mm", "fitness", 34999, "Amazon"),
    _item("Fitbit", "Charge 6", "fitness", 14999, "Flipkart"),
    _item("Noise", "ColorFit Pro 5", "fitness", 3999, "Flipkart"),
    # --- cameras ---
    _item("Sony", "Alpha ZV-E10 II Kit", "camera", 84990, "Amazon"),
    _item("Sony", "Alpha a7 IV Body", "camera", 214990, "Croma"),
    _item("Canon", "EOS R50 Kit", "camera", 67990, "Amazon"),
    _item("Canon", "EOS R8 Body", "camera", 144995, "Croma"),
    _item("Nikon", "Z50 II Kit", "camera", 79990, "Amazon"),
    _item("Fujifilm", "X-T30 II Kit", "camera", 99999, "Flipkart"),
    _item("GoPro", "HERO13 Black", "camera", 44990, "Amazon"),
    _item("DJI", "Osmo Pocket 3", "camera", 47990, "Amazon"),
    _item("DJI", "Mini 4 Pro Fly More Combo", "camera", 109900, "Croma"),
    _item("Insta360", "X4", "camera", 46990, "Amazon"),
    # --- travel ---
    _item("Generic", "Return Flights: Delhi to Bali (2 travellers)", "travel", 78000, "MakeMyTrip"),
    _item(
        "Generic", "Return Flights: Mumbai to Dubai (2 travellers)", "travel", 42000, "Cleartrip"
    ),
    _item(
        "Generic",
        "Return Flights: Bengaluru to Tokyo (2 travellers)",
        "travel",
        138000,
        "MakeMyTrip",
    ),
    _item("Generic", "Goa Beach Resort, 5 nights (2 travellers)", "travel", 46000, "Booking.com"),
    _item("Generic", "Kerala Houseboat Package, 4 nights", "travel", 34000, "MakeMyTrip"),
    _item("Generic", "Europe Rail Pass, 15 days", "travel", 62000, "Cleartrip"),
    _item("Generic", "Ladakh Motorcycle Tour, 8 days", "travel", 55000, "Thrillophilia"),
    _item("Generic", "Andaman Package, 6 nights (2 travellers)", "travel", 88000, "MakeMyTrip"),
    # --- vehicles ---
    _item("Honda", "Activa 6G", "vehicle", 84000, "Honda Dealer"),
    _item("TVS", "Jupiter 125", "vehicle", 89000, "TVS Dealer"),
    _item("Royal Enfield", "Hunter 350", "vehicle", 174000, "RE Dealer"),
    _item("Royal Enfield", "Classic 350", "vehicle", 219000, "RE Dealer"),
    _item("Bajaj", "Pulsar N160", "vehicle", 133000, "Bajaj Dealer"),
    _item("Yamaha", "MT-15 V2", "vehicle", 172000, "Yamaha Dealer"),
    _item("Ola", "S1 Pro Gen 3", "vehicle", 148000, "Ola Electric"),
    _item("Ather", "450X 3.7kWh", "vehicle", 159000, "Ather Space"),
    _item("Hero", "Splendor Plus", "vehicle", 78000, "Hero Dealer"),
    _item("Suzuki", "Access 125", "vehicle", 88000, "Suzuki Dealer"),
)

BY_ID: dict[str, CatalogItem] = {item.external_id: item for item in CATALOG}
