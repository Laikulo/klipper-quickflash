try:
    from .version_gen import KQF_PACK_VERSION, KQF_PACK_GITHASH, KQF_PACK_DATE
except ImportError:
    KQF_PACK_VERSION = None
    KQF_PACK_GITHASH = None
    KQF_PACK_DATE = None
    pass

KQF_VERSION = KQF_PACK_VERSION if KQF_PACK_VERSION else "?"
KQF_GITHASH = KQF_PACK_GITHASH if KQF_PACK_GITHASH else "unknown"
KQF_DATE = KQF_PACK_DATE if KQF_PACK_DATE else "????-??-??"
