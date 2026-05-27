from datetime import datetime, timezone

from .models import Signal


def format_signals(signals: list[Signal]) -> str:
    if not signals:
        return "📊 Funding Scan | No signals"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"📊 Funding Scan | {now} | {len(signals)} signals", ""]

    for s in signals:
        basis_sign = "+" if s.basis_pct >= 0 else ""
        line = (
            f"{s.exchange} {s.symbol} APY {s.apy_net:.1f}% "
            f"| gross {s.apy_gross:.1f}% cost {s.cost:.1f}% "
            f"| basis {basis_sign}{s.basis_pct:.2f}% spread {s.spread_bps:.1f}bp {s.interval_h}h"
        )
        lines.append(line)

    return "\n".join(lines)


def escape_md_v2(text: str) -> str:
    chars = r"_*[]()~`>#+-=|{}.!"
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text
