"""Formatting helpers and the fictional entities used by the generator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from app import gstin

D = Decimal
CENT = D("0.01")


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)


# --- number and date formatting --------------------------------------------------------------------


def fmt_amount(x: Decimal, lakh: bool = False, places: int = 2) -> str:
    """1234567.5 -> '12,34,567.50' (lakh) or '1,234,567.50' (western)."""
    x = x.quantize(D(1).scaleb(-places), rounding=ROUND_HALF_UP)
    neg = x < 0
    s = f"{abs(x):.{places}f}"
    whole, _, frac = s.partition(".")
    if lakh and len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts) + "," + tail
    else:
        whole = f"{int(whole):,}"
    out = whole + ("." + frac if frac else "")
    return "-" + out if neg else out


def fmt_qty(x: Decimal, places: int = 3) -> str:
    return f"{x:.{places}f}"


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(d: date, style: str) -> str:
    return {
        "dmy-slash": f"{d.day:02d}/{d.month:02d}/{d.year}",
        "dmy-dash": f"{d.day:02d}-{d.month:02d}-{d.year}",
        "dmy-dot": f"{d.day:02d}.{d.month:02d}.{d.year}",
        "d-mon-y": f"{d.day:02d}-{_MONTHS[d.month - 1]}-{d.year}",
        "d mon y": f"{d.day} {_MONTHS[d.month - 1]} {d.year}",
        "iso": d.isoformat(),
    }[style]


_ONES = (
    "Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen "
    "Sixteen Seventeen Eighteen Nineteen"
).split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_100(n: int) -> str:
    return _ONES[n] if n < 20 else _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])


def _below_1000(n: int) -> str:
    if n < 100:
        return _below_100(n)
    rest = "" if n % 100 == 0 else " " + _below_100(n % 100)
    return _ONES[n // 100] + " Hundred" + rest


def inr_words(x: Decimal) -> str:
    """Indian-system amount in words, e.g. 'Rupees One Lakh Twenty Three Thousand ... Only'."""
    x = q2(x)
    rupees, paise = int(x), int((x - int(x)) * 100)
    parts = []
    for div, name in ((10**7, "Crore"), (10**5, "Lakh"), (1000, "Thousand")):
        if rupees >= div:
            parts.append(f"{_below_100(rupees // div)} {name}")
            rupees %= div
    if rupees or not parts:
        parts.append(_below_1000(rupees))
    out = "Rupees " + " ".join(parts)
    if paise:
        out += f" and {_below_100(paise)} Paise"
    return out + " Only"


# --- entities --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    desc: str
    hsn: str
    uom: str
    price: tuple[int, int]  # INR range per unit
    qty: tuple[int, int]
    qty_places: int = 0  # decimals shown for quantity


@dataclass
class Supplier:
    key: str
    name: str
    short: str  # invoice number prefix
    addr: list[str]
    city: str
    state: str
    state_code: str
    pan: str
    entity: str
    layout: int
    rate: int  # GST rate percent
    items: list[Item]
    phone: str
    email: str
    bank: tuple[str, str, str]  # bank, account no, IFSC
    gstin: str = field(init=False)

    def __post_init__(self) -> None:
        self.gstin = gstin.make(self.state_code, self.pan, self.entity)


BUYER_NAME = "Larkfield Coffee Co."
BUYER_ADDR = ["Plot 14, Road No. 3, Banjara Hills", "Hyderabad, Telangana 500034"]
BUYER_STATE_CODE = "36"
BUYER_GSTIN = gstin.make("36", "AAKCL4417Q", "1")

GREEN = [
    Item("Arabica Plantation A, washed green bean", "09011111", "KG", (545, 640), (120, 900), 3),
    Item("Arabica Cherry AB, natural green bean", "09011112", "KG", (470, 560), (150, 800), 3),
    Item("Robusta Parchment AB, green bean", "09011121", "KG", (330, 395), (200, 1200), 3),
    Item("Robusta Cherry AA, green bean", "09011122", "KG", (290, 350), (250, 1500), 3),
    Item("Peaberry PB Arabica, washed", "09011111", "KG", (610, 720), (60, 300), 3),
    Item("Monsooned Malabar AA, green bean", "09011112", "KG", (520, 610), (80, 500), 3),
]
POUCH = [
    Item("Stand-up pouch 250 g, kraft, zip lock, valve", "39232990", "PCS", (5, 11), (3000, 40000)),
    Item("Aluminium foil bag 1 kg, one-way valve", "76072090", "PCS", (9, 19), (2000, 20000)),
    Item("Printed laminate roll stock 300 mm", "39219090", "KG", (210, 320), (40, 400), 2),
    Item("Tin tie closure strip, kraft", "39269099", "PCS", (1, 3), (10000, 60000)),
]
CARTON = [
    Item("Corrugated shipper carton 5-ply, 12x9x6 in", "48191010", "PCS", (22, 41), (500, 6000)),
    Item("Adhesive label roll 100x50 mm, 1000 per roll", "48211020", "ROLL", (240, 410), (20, 200)),
    Item("Kraft mailer box, 10x8x3 in", "48191010", "PCS", (14, 29), (800, 9000)),
    Item("Bubble wrap roll 1 m x 100 m", "39219090", "ROLL", (780, 1150), (5, 60)),
]
GEAR = [
    Item("Gooseneck kettle 1 L, stainless steel", "85167990", "PCS", (1450, 2400), (10, 120)),
    Item("Digital coffee scale 2 kg, 0.1 g", "90161000", "PCS", (780, 1350), (10, 150)),
    Item("V60 ceramic dripper size 02", "69111011", "PCS", (320, 560), (20, 300)),
    Item("Replacement burr set, hand grinder", "82089000", "SET", (410, 780), (15, 200)),
]
ROAST = [
    Item("Electric drum roaster 1 kg, single phase", "84198190", "NOS", (185000, 265000), (1, 2)),
    Item("Replacement heating element 2.2 kW", "85168000", "PCS", (3200, 5400), (2, 12)),
    Item("Cooling tray, stainless steel, 40 cm", "73239300", "PCS", (4100, 6900), (1, 4)),
    Item("Chaff collector assembly", "84219900", "NOS", (7800, 12600), (1, 3)),
]
HANDGEAR = [
    Item("Hand grinder 30 mm steel burr, aluminium body", "82100000", "PCS", (1650, 2700), (10, 120)),
    Item("Knock box, stainless steel, 21 cm", "73239300", "PCS", (690, 1250), (10, 100)),
    Item("Tamper 58 mm, walnut handle", "82055900", "PCS", (520, 980), (10, 150)),
    Item("Milk pitcher 600 ml, matte black", "73239300", "PCS", (480, 840), (12, 120)),
]
KITCHEN = [
    Item("French press 600 ml, borosilicate", "70139900", "PCS", (540, 930), (20, 250)),
    Item("Cupping spoon set of 6, stainless", "82159900", "SET", (780, 1380), (10, 90)),
    Item("Pour-over stand, bamboo", "44219990", "PCS", (460, 810), (10, 140)),
    Item("Ceramic mug 300 ml, speckle glaze", "69120010", "PCS", (170, 320), (60, 600)),
]
TRADER = [
    Item("Arabica Plantation B, washed green bean", "09011111", "KG", (500, 590), (150, 1000), 3),
    Item("Robusta Kaapi Royal, green bean", "09011122", "KG", (300, 365), (300, 1400), 3),
    Item("Chicory powder 25 kg sack", "21013000", "SACK", (2300, 3100), (4, 40)),
]
FREIGHT = [
    Item("Freight, full truck load, Chikmagalur to Hyderabad", "996511", "TRIP", (46000, 78000), (1, 2)),
    Item("Freight, part load, Coimbatore to Hyderabad", "996511", "TRIP", (14000, 29000), (1, 3)),
    Item("Loading and unloading charges", "996719", "LOT", (3500, 7800), (1, 2)),
    Item("Warehouse handling per pallet", "996729", "PLT", (180, 340), (10, 80)),
]

SUPPLIERS: list[Supplier] = [
    Supplier(
        "kaveri",
        "Kaveri Ridge Estates Pvt Ltd",
        "KRE",
        ["Survey No. 118, Baba Budan Road", "Chikmagalur, Karnataka 577101"],
        "Chikmagalur",
        "Karnataka",
        "29",
        "AABCK3381M",
        "1",
        1,
        5,
        GREEN[:5],
        "+91 80 4120 7733",
        "accounts@kaveriridge.example",
        ("Canara Bank, Chikmagalur", "0412201004419", "CNRB0000412"),
    ),
    Supplier(
        "wayanad",
        "Wayanad Mist Plantations LLP",
        "WMP",
        ["Kalpetta Estate Office, Vythiri Road", "Wayanad, Kerala 673121"],
        "Wayanad",
        "Kerala",
        "32",
        "AAKFW7742D",
        "1",
        1,
        5,
        GREEN[2:],
        "+91 493 620 5518",
        "billing@wayanadmist.example",
        ("Federal Bank, Kalpetta", "10250100087312", "FDRL0001025"),
    ),
    Supplier(
        "sundaram",
        "Sundaram Flexipack Industries Pvt Ltd",
        "SFI",
        ["Unit II, SIDCO Industrial Estate", "Ambattur, Chennai, Tamil Nadu 600098"],
        "Chennai",
        "Tamil Nadu",
        "33",
        "AAECS5126H",
        "1",
        2,
        18,
        POUCH,
        "+91 44 2625 9140",
        "sales@sundaramflexi.example",
        ("Indian Bank, Ambattur", "6218843307", "IDIB000A056"),
    ),
    Supplier(
        "deccan",
        "Deccan Kraft & Labels Pvt Ltd",
        "DKL",
        ["Plot 62, IDA Cherlapally", "Hyderabad, Telangana 500051"],
        "Hyderabad",
        "Telangana",
        "36",
        "AAFCD9065R",
        "1",
        2,
        18,
        CARTON,
        "+91 40 2727 4410",
        "orders@deccankraft.example",
        ("HDFC Bank, Cherlapally", "50200041873562", "HDFC0001720"),
    ),
    Supplier(
        "bharat",
        "Bharat Brewgear Works",
        "BBW",
        ["B-27, Okhla Industrial Area, Phase II", "New Delhi, Delhi 110020"],
        "New Delhi",
        "Delhi",
        "07",
        "AAHFB2279K",
        "1",
        3,
        18,
        GEAR,
        "+91 11 4152 8870",
        "sales@bharatbrewgear.example",
        ("State Bank of India, Okhla", "37481209954", "SBIN0001693"),
    ),
    Supplier(
        "hind",
        "Hind Roastech Engineers",
        "HRE",
        ["Shed 9, Jeedimetla Industrial Area", "Hyderabad, Telangana 500055"],
        "Hyderabad",
        "Telangana",
        "36",
        "AAGFH6318C",
        "1",
        3,
        18,
        ROAST,
        "+91 40 2309 6612",
        "support@hindroastech.example",
        ("ICICI Bank, Jeedimetla", "003405017766", "ICIC0000034"),
    ),
    Supplier(
        "coorg",
        "Coorg Grindworks & Fabricators",
        "CGF",
        ["12/3, Mercara Road", "Madikeri, Karnataka 571201"],
        "Madikeri",
        "Karnataka",
        "29",
        "AAMFC4890P",
        "1",
        4,
        18,
        HANDGEAR,
        "+91 8272 22 4471",
        "works@coorggrind.example",
        ("Karnataka Bank, Madikeri", "4217000100236918", "KARB0000421"),
    ),
    Supplier(
        "pearl",
        "Pearl City Kitchenware Pvt Ltd",
        "PCK",
        ["Door 5-9-221, Chirag Ali Lane", "Hyderabad, Telangana 500001"],
        "Hyderabad",
        "Telangana",
        "36",
        "AAFCP1857L",
        "1",
        4,
        18,
        KITCHEN,
        "+91 40 2461 3059",
        "accounts@pearlcityware.example",
        ("Axis Bank, Abids", "917020044518803", "UTIB0000046"),
    ),
    Supplier(
        "blueridge",
        "Blue Ridge Beans Trading Co",
        "BRB",
        ["88, Race Course Road", "Coimbatore, Tamil Nadu 641018"],
        "Coimbatore",
        "Tamil Nadu",
        "33",
        "AAJFB3306G",
        "1",
        5,
        5,
        TRADER,
        "+91 422 4391 118",
        "trade@blueridgebeans.example",
        ("Karur Vysya Bank, Race Course", "1156135000071204", "KVBL0001156"),
    ),
    Supplier(
        "greenline",
        "Greenline Freight Services",
        "GFS",
        ["Warehouse Row 4, Medchal Road", "Hyderabad, Telangana 500067"],
        "Hyderabad",
        "Telangana",
        "36",
        "AAIFG7761E",
        "1",
        5,
        18,
        FREIGHT,
        "+91 40 6666 1204",
        "billing@greenlinefreight.example",
        ("Kotak Mahindra Bank, Medchal", "7412118806", "KKBK0007412"),
    ),
]
SUPPLIER_BY_KEY = {s.key: s for s in SUPPLIERS}


@dataclass(frozen=True)
class Courier:
    key: str
    name: str
    addr: list[str]
    gstin: str
    awb_prefix: str
    awb_digits: int
    layout: int


COURIERS = [
    Courier(
        "swiftline",
        "Swiftline Express Couriers Pvt Ltd",
        ["14, Transport Nagar", "Hyderabad, Telangana 500018"],
        gstin.make("36", "AAGCS8827N", "1"),
        "SLX",
        9,
        1,
    ),
    Courier(
        "bluekite",
        "BlueKite Logistics India Pvt Ltd",
        ["Tower B, Prestige Tech Park", "Bengaluru, Karnataka 560103"],
        gstin.make("29", "AAHCB5304J", "1"),
        "",
        12,
        2,
    ),
]

RECEIVERS = [
    "R. Venkatesh",
    "Sana Fatima",
    "K. Lakshmi Devi",
    "Mohammed Irfan",
    "P. Sridhar",
    "Anita Reddy",
    "T. Naresh Kumar",
    "Farhan Ali",
    "G. Padmavathi",
    "S. Karthik",
]
