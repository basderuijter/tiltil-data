"""Verzending: Sendcloud-labels en printen, verhuisd uit de losse label-scanner.

De scan-app aan de inpaktafel is vervallen. De PH-medewerker legt de producten
in kratten — één krat is één doos — dus daar is bekend hoeveel labels er nodig
zijn en wat er in elke doos zit. Pakbon en label gaan mee in de krat.
"""

from .config import VerzendFout, VerzendInstellingen, laad_verzendinstellingen
from .kratten import Krat, KratRegel, is_giftcard
from .models import Label, Order, OrderItem, ShippingOption
from .printing import PrintError
from .sendcloud import OrderNotFound, SendcloudError
from .service import Adres, Verzendservice, ZendingResultaat
from .stations import Station, StationError

__all__ = [
    "Adres",
    "Krat",
    "KratRegel",
    "Label",
    "Order",
    "OrderItem",
    "OrderNotFound",
    "PrintError",
    "SendcloudError",
    "ShippingOption",
    "Station",
    "StationError",
    "VerzendFout",
    "VerzendInstellingen",
    "Verzendservice",
    "ZendingResultaat",
    "is_giftcard",
    "laad_verzendinstellingen",
]
