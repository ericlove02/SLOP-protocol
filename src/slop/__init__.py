"""SLOP: the Semantically Lossy Object Protocol.

>>> import slop
>>> packet = slop.encode("photo.jpg")          # image -> words, in a packet
>>> image = slop.decode(packet)                 # words -> an image. Probably that one.
"""

from slop.decoder import DecodeResult, decode, decode_detailed
from slop.encoder import BudgetError, EncodeResult, encode, encode_detailed
from slop.packet import Compression, Packet, PacketError

__version__ = "0.1.0"

__all__ = [
    "BudgetError",
    "Compression",
    "DecodeResult",
    "EncodeResult",
    "Packet",
    "PacketError",
    "__version__",
    "decode",
    "decode_detailed",
    "encode",
    "encode_detailed",
]
