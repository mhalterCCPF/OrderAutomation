import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from modules.config import load_config, load_secrets
from modules.conductor_gui import ConductorWindow
from modules.gui_config import ConfigDialog
from modules.order_workflow import WorkflowOrchestrator

class AppLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Shopify UV Print Automation")
        self.geometry("400x360")
        self.resizable(False, False)

        ttk.Label(self, text="Order Processing Workflow", font=("Helvetica", 14, "bold")).pack(pady=15)

        # Buttons
        ttk.Button(self, text="Get Next Order", command=self.handle_next_order, width=30).pack(pady=8)
        ttk.Button(self, text="Get Specific Order", command=self.handle_specific_order, width=30).pack(pady=8)
        ttk.Button(self, text="Set Configurations", command=self.open_configs, width=30).pack(pady=8)
        ttk.Button(self, text="Conductor", command=self.open_conductor, width=30).pack(pady=8)
        ttk.Button(self, text="Exit", command=self.destroy, width=30).pack(pady=(15, 8))

    def get_service(self) -> WorkflowOrchestrator:
        return WorkflowOrchestrator(
            {**load_config(), **load_secrets()},
            print_message_callback=lambda message: messagebox.showinfo("Print Queue", message, parent=self),
        )

    def handle_next_order(self):
        service = self.get_service()
        ok, msg = service.process_next_order()
        messagebox.showinfo("Result" if ok else "Error", msg)

    def handle_specific_order(self):
        order_num = simpledialog.askstring("Input", "Enter Order Number:", parent=self)
        if not order_num:
            return

        service = self.get_service()
        ok, msg = service.process_specific_order(order_num)
        messagebox.showinfo("Result" if ok else "Error", msg)

    def open_configs(self):
        dialog = ConfigDialog(self)
        dialog.grab_set()

    def open_conductor(self):
        self.withdraw()
        try:
            ConductorWindow(
                self,
                {**load_config(), **load_secrets()},
                load_config(),
            )
        except Exception as error:
            self.deiconify()
            messagebox.showerror("Conductor", str(error), parent=self)

if __name__ == "__main__":
    app = AppLauncher()
    app.mainloop()