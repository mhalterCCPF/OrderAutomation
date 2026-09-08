import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from modules.config import load_config, save_config

class ConfigDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Set Configurations")
        self.geometry("560x430")
        self.resizable(True, False)
        
        self.config = load_config()

        # Print flags
        self.var_summary = tk.BooleanVar(value=self.config.get("packing_slip", True))
        self.var_label = tk.BooleanVar(value=self.config.get("print_mailing_label", True))
        self.var_queue = tk.BooleanVar(value=self.config.get("queue_multi_print_orders", False))
        self.var_cleanup = tk.BooleanVar(value=self.config.get("cleanup", False))
        self.var_dir = tk.StringVar(value=self.config.get("eufymake_dir", ""))
        self.var_assets_dir = tk.StringVar(value=self.config.get("downloaded_assets_dir", ""))
        self.var_slip_dir = tk.StringVar(value=self.config.get("packing_slip_dir", ""))

        # UI Layout
        ttk.Checkbutton(self, text="Packing Slip PDF", variable=self.var_summary).pack(anchor="w", padx=20, pady=10)
        ttk.Checkbutton(self, text="Print Mailing Label", variable=self.var_label).pack(anchor="w", padx=20, pady=5)
        ttk.Checkbutton(self, text="Queue multi-print orders", variable=self.var_queue).pack(anchor="w", padx=20, pady=5)
        ttk.Checkbutton(self, text="Clean-up", variable=self.var_cleanup).pack(anchor="w", padx=20, pady=5)

        self._add_directory_row("EufyMake Input Directory:", self.var_dir)
        self._add_directory_row("Downloaded Assets Directory:", self.var_assets_dir)
        self._add_directory_row("Packing Slip Directory:", self.var_slip_dir)

        ttk.Button(self, text="Save Settings", command=self._save).pack(pady=15)

    def _add_directory_row(self, label: str, variable: tk.StringVar):
        ttk.Label(self, text=label).pack(anchor="w", padx=20, pady=(8, 2))
        frame = ttk.Frame(self)
        frame.pack(fill="x", padx=20, pady=2)
        ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse", command=lambda: self._browse_dir(variable)).pack(side="right", padx=(5, 0))

    def _browse_dir(self, variable: tk.StringVar):
        selected = filedialog.askdirectory(initialdir=variable.get())
        if selected:
            variable.set(selected)

    def _save(self):
        self.config["packing_slip"] = self.var_summary.get()
        self.config["print_mailing_label"] = self.var_label.get()
        self.config["queue_multi_print_orders"] = self.var_queue.get()
        self.config["cleanup"] = self.var_cleanup.get()
        self.config["eufymake_dir"] = self.var_dir.get()
        self.config["downloaded_assets_dir"] = self.var_assets_dir.get()
        self.config["packing_slip_dir"] = self.var_slip_dir.get()
        save_config(self.config)
        messagebox.showinfo("Success", "Configuration saved.", parent=self)
        self.destroy()