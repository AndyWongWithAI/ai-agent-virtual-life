# location -> list of adjacent locations
ADJACENCY = {
    "李四家": ["客厅", "厨房"],
    "王五家": ["客厅", "厨房", "公园"],
    "客厅": ["李四家", "王五家", "厨房"],
    "厨房": ["李四家", "王五家", "客厅"],
    "公园": ["王五家"],
}


def neighbors(location: str) -> list[str]:
    return ADJACENCY.get(location, [])
