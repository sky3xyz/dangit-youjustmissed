from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

def parse_csv_line(line: str) -> list[str]:
    fields: list[str] = []
    field: list[str] = []
    in_quotes = False
    i = 0
    while i < len(line):
        c = line[i]
        if in_quotes:
            if c == '"':
                if i + 1 < len(line) and line[i + 1] == '"':
                    field.append('"')
                    i += 1  # skip the escaped quote
                else:
                    in_quotes = False
            else:
                field.append(c)
        elif c == '"':
            in_quotes = True
        elif c == ",":
            fields.append("".join(field))
            field = []
        else:
            field.append(c)
        i += 1
    fields.append("".join(field))
    return fields


def hms_to_sec(t: str) -> int:
    """
    convert hh:mm:ss to seconds
    """
    hh, mm, ss = (int(part) for part in t.split(":"))
    return hh * 3600 + mm * 60 + ss


def sec_to_clock(sec: int) -> str:
    """
    seconds to hh:mm:ss

    """
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h % 24:02d}:{m:02d}:{s:02d}"


def ymd_of(d: datetime, tz: ZoneInfo) -> str:
    """convert date based on tz"""
    return d.astimezone(tz).strftime("%Y%m%d")


def secs_now(d: datetime, tz: ZoneInfo) -> int:
    """current time in tz"""
    local = d.astimezone(tz)
    return local.hour * 3600 + local.minute * 60 + local.second


def shift_ymd(ymd: str, days: int) -> str:
    base = datetime.strptime(ymd, "%Y%m%d")
    shifted = base + timedelta(days=days)
    return shifted.strftime("%Y%m%d")


def epoch_ms_for(ymd: str, secs: int) -> int:
    base_ms = int(datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=ZoneInfo("UTC")).timestamp() * 1000)
    return base_ms + secs * 1000
