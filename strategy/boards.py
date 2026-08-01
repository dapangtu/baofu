"""
板块分类工具

A股板块划分（按代码前缀）:
- 沪市主板: 600/601/603/605
- 深市主板: 000/001/002/003
- 创业板:   300/301
- 科创板:   688/689
- 北交所:   4xxxxx/8xxxxx/920xxx
- B股:      900/200
"""


def board_of(code) -> str:
    """返回股票所属板块，未知返回 other。"""
    c = str(code).zfill(6)
    if c.startswith(('600', '601', '603', '605')):
        return 'sh_main'
    if c.startswith(('000', '001', '002', '003')):
        return 'sz_main'
    if c.startswith(('300', '301')):
        return 'chinext'
    if c.startswith(('688', '689')):
        return 'star'
    if c.startswith(('900', '200')):
        return 'b'
    if c.startswith(('4', '8', '92')):
        return 'beijing'
    return 'other'


def board_label(board: str) -> str:
    return {
        'sh_main': '沪市主板',
        'sz_main': '深市主板',
        'chinext': '创业板',
        'star': '科创板',
        'b': 'B股',
        'beijing': '北交所',
        'other': '其他',
    }.get(board, board)


def is_selected_board(board: str) -> bool:
    """是否属于要选的板块：主板 + 创业板。"""
    return board in ('sh_main', 'sz_main', 'chinext')


def main_board_mask(codes) -> "list[bool]":
    """主板块（沪深主板，不含创业板）掩码。"""
    return [board_of(c) in ('sh_main', 'sz_main') for c in codes]
