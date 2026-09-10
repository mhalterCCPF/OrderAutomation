import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from modules.config import load_config, save_config

class ConfigDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("Set Configurations")
        self.geometry("560x650")
        self.resizable(True, False)

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.content_frame = ttk.Frame(canvas)
        self.content_frame.bind(
            "<Configure>",
            lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=self.content_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"))
        
        self.config = load_config()
        company = self.config.get("company", {})

        # Print flags
        self.var_summary = tk.BooleanVar(value=self.config.get("packing_slip", True))
        self.var_attach_slip = tk.BooleanVar(value=self.config.get("add_packing_slip_to_order", True))
        self.var_label = tk.BooleanVar(value=self.config.get("print_mailing_label", True))
        self.var_queue = tk.BooleanVar(value=self.config.get("queue_multi_print_orders", False))
        self.var_cleanup = tk.BooleanVar(value=self.config.get("cleanup", False))
        self.var_dir = tk.StringVar(value=self.config.get("eufymake_dir", ""))
        self.var_assets_dir = tk.StringVar(value=self.config.get("downloaded_assets_dir", ""))
        self.var_slip_dir = tk.StringVar(value=self.config.get("packing_slip_dir", ""))
        self.var_company_name = tk.StringVar(value=company.get("name", ""))
        self.var_company_address = tk.StringVar(value=company.get("address_line1", ""))
        self.var_company_city_state_zip = tk.StringVar(value=company.get("city_state_zip", ""))
        self.var_company_email = tk.StringVar(value=company.get("email", ""))
        self.var_company_website = tk.StringVar(value=company.get("website", ""))

        # UI Layout
        ttk.Checkbutton(self.content_frame, text="Packing Slip PDF", variable=self.var_summary).pack(anchor="w", padx=20, pady=10)
        ttk.Checkbutton(self.content_frame, text="Add Packing Slip to Order", variable=self.var_attach_slip).pack(anchor="w", padx=20, pady=5)
        ttk.Checkbutton(self.content_frame, text="Print Mailing Label", variable=self.var_label).pack(anchor="w", padx=20, pady=5)
        ttk.Checkbutton(self.content_frame, text="Queue multi-print orders", variable=self.var_queue).pack(anchor="w", padx=20, pady=5)
        ttk.Checkbutton(self.content_frame, text="Clean-up", variable=self.var_cleanup).pack(anchor="w", padx=20, pady=5)

        self._add_directory_row("Print Folder:", self.var_dir)
        self._add_directory_row("Downloaded Assets Directory:", self.var_assets_dir)
        self._add_directory_row("Packing Slip Directory:", self.var_slip_dir)

        ttk.Label(self.content_frame, text="Company Information").pack(anchor="w", padx=20, pady=(12, 2))
        self._add_text_row("Company Name:", self.var_company_name)
        self._add_text_row("Company Address:", self.var_company_address)
        self._add_text_row("City, State, ZIP:", self.var_company_city_state_zip)
        self._add_text_row("Company Email:", self.var_company_email)
        self._add_text_row("Company Website:", self.var_company_website)

        ttk.Button(self.content_frame, text="Save Settings", command=self._save).pack(pady=15)

    def _add_directory_row(self, label: str, variable: tk.StringVar):
        ttk.Label(self.content_frame, text=label).pack(anchor="w", padx=20, pady=(8, 2))
        frame = ttk.Frame(self.content_frame)
        frame.pack(fill="x", padx=20, pady=2)
        ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True)
        ttk.Button(frame, text="Browse", command=lambda: self._browse_dir(variable)).pack(side="right", padx=(5, 0))

    def _add_text_row(self, label: str, variable: tk.StringVar):
        ttk.Label(self.content_frame, text=label).pack(anchor="w", padx=20, pady=(5, 2))
        ttk.Entry(self.content_frame, textvariable=variable).pack(fill="x", padx=20, pady=2)

    def _browse_dir(self, variable: tk.StringVar):
        selected = filedialog.askdirectory(initialdir=variable.get())
        if selected:
            variable.set(selected)

    def _save(self):
        self.config["packing_slip"] = self.var_summary.get()
        self.config["add_packing_slip_to_order"] = self.var_attach_slip.get()
        self.config["print_mailing_label"] = self.var_label.get()
        self.config["queue_multi_print_orders"] = self.var_queue.get()
        self.config["cleanup"] = self.var_cleanup.get()
        self.config["eufymake_dir"] = self.var_dir.get()
        self.config["downloaded_assets_dir"] = self.var_assets_dir.get()
        self.config["packing_slip_dir"] = self.var_slip_dir.get()
        self.config["company"] = {
            "name": self.var_company_name.get(),
            "address_line1": self.var_company_address.get(),
            "city_state_zip": self.var_company_city_state_zip.get(),
            "email": self.var_company_email.get(),
            "website": self.var_company_website.get(),
        }
        save_config(self.config)
        messagebox.showinfo("Success", "Configuration saved.", parent=self)
        self.destroy()