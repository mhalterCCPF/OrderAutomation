"""Tkinter view and lifecycle controls for the Conductor."""

from __future__ import annotations

import tkinter as tk
import time
from tkinter import messagebox, ttk

from modules.conductor import ConductorService
from modules.conductor_server import ConductorHTTPServer, load_or_create_token
from modules.conductor_state import ConductorState
from modules.order_workflow import WorkflowOrchestrator


class ConductorWindow(tk.Toplevel):
    def __init__(self, parent, config: dict, service_config: dict):
        super().__init__(parent)
        self.parent = parent
        self.title("OrderAutomation | Conductor")
        self.geometry("940x650")
        self.minsize(760, 500)
        self.protocol("WM_DELETE_WINDOW", self.stop_conductor)
        self.state_store = ConductorState()
        workflow = WorkflowOrchestrator(config)
        self.service = ConductorService(workflow, self.state_store, service_config)
        self.token = load_or_create_token()
        try:
            self.http_server = ConductorHTTPServer(
                self.service,
                service_config.get("conductor_host", "0.0.0.0"),
                int(service_config.get("conductor_port", 8765)),
                self.token,
            )
            self.http_server.start()
        except Exception:
            self.destroy()
            raise
        self.service.start()

        controls = ttk.Frame(self, padding=10)
        controls.pack(fill="x")
        self.pause_button = ttk.Button(controls, text="Pause Conductor", command=self.toggle_polling)
        self.pause_button.pack(side="left")
        ttk.Button(controls, text="Stop Conductor", command=self.stop_conductor).pack(side="left", padx=(8, 0))
        ttk.Label(controls, text=f"Loader API: {self.http_server.address[0]}:{self.http_server.address[1]}").pack(side="right")
        self.poll_status_var = tk.StringVar(value="Polling Shopify")
        ttk.Label(self, textvariable=self.poll_status_var, padding=(10, 0, 10, 6)).pack(anchor="w")

        token_row = ttk.Frame(self, padding=(10, 0, 10, 8))
        token_row.pack(fill="x")
        ttk.Label(token_row, text="Shared loader token").pack(side="left", padx=(0, 8))
        token_entry = ttk.Entry(token_row)
        token_entry.insert(0, self.token)
        token_entry.configure(state="readonly")
        token_entry.pack(side="left", fill="x", expand=True)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.trees = {
            "Queued Orders": self._add_table("Queued Orders", ("order", "id")),
            "Completed Orders not yet in Shopify": self._add_table(
                "Completed Orders not yet in Shopify", ("order", "loader")
            ),
            "Registered eufyLoaders": self._add_table(
                "Registered eufyLoaders", ("loader", "status", "order", "heartbeat")
            ),
            "Active Assignments": self._add_table(
                "Active Assignments", ("order", "loader", "status", "error")
            ),
        }
        requeue_row = ttk.Frame(self)
        requeue_row.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(requeue_row, text="Requeue Selected Interrupted Order", command=self.requeue_selected).pack(side="right")
        self.refresh_after_id = self.after(500, self.refresh_views)

    def _add_table(self, title: str, columns: tuple[str, ...]) -> ttk.Treeview:
        frame = ttk.Frame(self.notebook, padding=8)
        tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("order", text="Order") if "order" in columns else None
        tree.heading("id", text="Order ID") if "id" in columns else None
        tree.heading("loader", text="Loader") if "loader" in columns else None
        tree.heading("status", text="Status") if "status" in columns else None
        tree.heading("heartbeat", text="Last heartbeat") if "heartbeat" in columns else None
        tree.heading("error", text="Error") if "error" in columns else None
        for column in columns:
            tree.column(column, width=180, stretch=True)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.notebook.add(frame, text=title)
        return tree

    def toggle_polling(self) -> None:
        if self.service.polling_paused.is_set():
            self.service.resume_polling()
            self.pause_button.configure(text="Pause Conductor")
        else:
            self.service.pause_polling()
            self.pause_button.configure(text="Resume Conductor")

    def refresh_views(self) -> None:
        if not self.winfo_exists():
            return
        if self.service.polling_paused.is_set():
            self.poll_status_var.set("Shopify polling paused; loader service remains active")
        elif self.service.last_poll_error:
            self.poll_status_var.set(f"Shopify poll failed: {self.service.last_poll_error}")
        else:
            self.poll_status_var.set("Polling Shopify")
        state = self.state_store.snapshot()
        self._replace_rows(self.trees["Queued Orders"], [
            (order.get("name", order_id), order_id)
            for order_id, order in state["queued_orders"].items()
        ])
        self._replace_rows(self.trees["Completed Orders not yet in Shopify"], [
            (item.get("order", {}).get("name", order_id), item.get("loader_id", ""))
            for order_id, item in state["completed_orders"].items()
        ])
        self._replace_rows(self.trees["Registered eufyLoaders"], [
            (loader_id, loader.get("status", "unknown"), loader.get("order_id") or "", self._format_heartbeat(loader.get("last_seen")))
            for loader_id, loader in state["registered_loaders"].items()
        ])
        self._replace_rows(self.trees["Active Assignments"], [
            (item.get("order", {}).get("name", order_id), item.get("loader_id", ""), item.get("status", ""), item.get("error", ""))
            for order_id, item in state["active_assignments"].items()
        ], row_ids=list(state["active_assignments"]))
        self.refresh_after_id = self.after(1000, self.refresh_views)

    @staticmethod
    def _format_heartbeat(value) -> str:
        if isinstance(value, (int, float)):
            return f"{max(0, int(time.time() - value))} sec ago"
        return str(value or "")

    @staticmethod
    def _replace_rows(tree: ttk.Treeview, rows: list[tuple], row_ids: list[str] | None = None) -> None:
        for item in tree.get_children():
            tree.delete(item)
        for index, values in enumerate(rows):
            iid = row_ids[index] if row_ids else None
            tree.insert("", "end", iid=iid, values=values)

    def requeue_selected(self) -> None:
        tree = self.trees["Active Assignments"]
        selected = tree.selection()
        if not selected:
            messagebox.showinfo("Requeue", "Select an interrupted assignment first.", parent=self)
            return
        order_id = selected[0]
        try:
            self.service.requeue_interrupted(order_id)
        except Exception as error:
            messagebox.showerror("Requeue failed", str(error), parent=self)
        else:
            return

    def stop_conductor(self) -> None:
        self.service.stop()
        self.http_server.stop()
        if self.refresh_after_id:
            try:
                self.after_cancel(self.refresh_after_id)
            except tk.TclError:
                pass
        self.destroy()
        self.parent.deiconify()
        self.parent.lift()
