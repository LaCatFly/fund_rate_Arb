from datetime import datetime, timezone

from .models import Signal


def format_signals(signals: list[Signal], title: str = "Funding Signals") -> str:
    """Compact TG-friendly table wrapped in code block for monospace rendering."""
    if not signals:
        return f"📊 {title} | No signals"

    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    n = len(signals)

    lines = [
        f"📊 {title} — {n} ranked | {now} | weekly rebalance",
        "",
        "```",
        f"{'#':<3} {'Sym':<5} {'Ex':<3} {'Net%':<6} {'Basis':<7} {'Sprd':<5} {'R':<4} {'72hµ%':<8} {'Score':<6} {'h':<3}",
        "-" * 57,
    ]

    for i, s in enumerate(signals, 1):
        b = f"{s.basis_pct:+7.3f}"
        sp = f"{s.spread_bps:.1f}bp"
        avg = f"{s.avg_rate_72h*100:.4f}" if hasattr(s, 'avg_rate_72h') and s.avg_rate_72h else "N/A"
        r = f"{s.positive_ratio_72h:.2f}" if hasattr(s, 'positive_ratio_72h') else "N/A"
        score = f"{s.score_weekly:.1f}" if hasattr(s, 'score_weekly') and s.score_weekly else f"{s.apy_net:.1f}"

        lines.append(
            f"{i:<3} {s.symbol:<5} {s.exchange:<3} {s.apy_net:<6.1f} {b:<7} {sp:<5} {r:<4} {avg:<8} {score:<6} {s.interval_h:<3}"
        )

    lines.append("```")
    lines.extend([
        "",
        "R = +Ratio 72h | Score = weekly rebalance",
    ])

    return "\n".join(lines)


def escape_md_v2(text: str) -> str:
    chars = r"_*[]()~`>#+-=|{}.!\\"
    for c in chars:
        text = text.replace(c, f"\\{c}")
    return text
