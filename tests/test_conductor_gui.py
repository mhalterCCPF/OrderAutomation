from modules.conductor_gui import ConductorWindow


class FakeTreeview:
    def __init__(self, rows):
        self.rows = dict(rows)
        self.selected = next(iter(self.rows), None)
        self.deleted = []

    def get_children(self):
        return list(self.rows)

    def delete(self, item_id):
        self.deleted.append(item_id)
        self.rows.pop(item_id, None)
        if self.selected == item_id:
            self.selected = None

    def exists(self, item_id):
        return item_id in self.rows

    def item(self, item_id, option=None, **kwargs):
        if kwargs:
            self.rows[item_id] = kwargs["values"]
        if option == "values":
            return self.rows[item_id]

    def insert(self, _parent, _index, iid, values):
        self.rows[iid] = values


def test_table_refresh_preserves_selected_assignment_row():
    tree = FakeTreeview({"order-1": ("#1001", "loader-1", "interrupted", "timeout")})

    ConductorWindow._replace_rows(
        tree,
        [("#1001", "loader-1", "interrupted", "timeout")],
        row_ids=["order-1"],
    )

    assert tree.selected == "order-1"
    assert tree.deleted == []


def test_table_refresh_updates_changed_values_without_replacing_row():
    tree = FakeTreeview({"order-1": ("#1001", "loader-1", "interrupted", "timeout")})

    ConductorWindow._replace_rows(
        tree,
        [("#1001", "loader-1", "processing", "")],
        row_ids=["order-1"],
    )

    assert tree.rows["order-1"] == ("#1001", "loader-1", "processing", "")
    assert tree.deleted == []