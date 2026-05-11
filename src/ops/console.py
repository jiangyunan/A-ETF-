"""
Rich 控制台美化 — 统一风格，所有模块共用。

用法:
    from src.ops.console import console, print_header, print_metric, print_table

颜色约定（适配暗色终端背景）:
    header  — 亮蓝色加粗 (章节标题)
    success — 亮绿色 (PASS/完成)
    warning — 亮黄色 (WARN/注意)
    danger  — 亮红色 (FAIL/错误)
    info    — 亮青色 (信息)
    money   — 亮绿色 (金额)
    dim     — 亮灰色 (辅助文字)
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich import box

# 全局 Rich Console（单例）
# 暗色终端适配：force_terminal=True 确保颜色正确渲染
console = Console()


def print_header(title: str, width: int = 60) -> None:
    """打印章节标题（亮蓝色加粗 + 分隔线）。"""
    console.rule(f"[bold bright_blue]{title}[/bold bright_blue]", characters="─", style="bright_blue")


def print_subheader(title: str) -> None:
    """打印子标题（亮青色）。"""
    console.print(f"[bold bright_cyan]▎{title}[/bold bright_cyan]")


def print_success(msg: str) -> None:
    """亮绿色成功消息。"""
    console.print(f"  [bold bright_green]✅ {msg}[/bold bright_green]")


def print_warning(msg: str) -> None:
    """亮黄色警告消息。"""
    console.print(f"  [bold bright_yellow]⚠️  {msg}[/bold bright_yellow]")


def print_error(msg: str) -> None:
    """亮红色错误消息。"""
    console.print(f"  [bold bright_red]❌ {msg}[/bold bright_red]")


def print_info(msg: str) -> None:
    """亮青色信息消息。"""
    console.print(f"  [bright_cyan]• {msg}[/bright_cyan]")


def print_dim(msg: str) -> None:
    """亮灰色辅助文字（比 dim 更亮，暗色背景可见）。"""
    console.print(f"  [bright_black]{msg}[/bright_black]")


def print_money(label: str, amount: float) -> None:
    """金额显示（亮绿色加粗）。"""
    console.print(f"  {label}: [bold bright_green]¥{amount:,.0f}[/bold bright_green]")


def print_metric(label: str, value: str) -> None:
    """指标键值对（左标签 + 右值）。"""
    console.print(f"  [bold]{label}:[/bold] {value}")


def print_key_value(key: str, value: str, indent: int = 2) -> None:
    """通用键值对输出。"""
    pad = " " * indent
    console.print(f"{pad}[bold]{key}:[/bold] {value}")


def make_table(title: str, columns: list[str], rows: list[list[str]],
               styles: list[str] | None = None) -> Table:
    """
    创建 Rich 表格（暗色背景优化）。

    Args:
        title: 表格标题
        columns: 列名列表
        rows: 数据行 (每行是字符串列表)
        styles: 每列的对齐方式 ('left'/'right'/'center')

    Returns:
        Table 对象 (需 console.print() 输出)
    """
    if styles is None:
        styles = ["left"] * len(columns)

    table = Table(
        title=f"[bold bright_blue]{title}[/bold bright_blue]",
        box=box.ROUNDED,
        header_style="bold bright_cyan",
        title_style="bold",
        border_style="bright_blue",
    )
    for col, style in zip(columns, styles):
        table.add_column(col, justify=style, no_wrap=True)

    for row in rows:
        table.add_row(*row)

    return table


def print_panel(content: str, title: str = "", style: str = "bright_blue") -> None:
    """打印带边框的面板。"""
    panel = Panel(
        Text(content),
        title=title,
        border_style=style,
        padding=(1, 2),
    )
    console.print(panel)


def print_divider(char: str = "─", style: str = "bright_black") -> None:
    """打印分隔线。"""
    console.rule(style=style, characters=char)
