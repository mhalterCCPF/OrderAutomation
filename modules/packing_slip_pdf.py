from pathlib import Path
from datetime import datetime

from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML


def _format_order_date(value: str) -> str:
    if not value:
        return ""
    try:
        order_date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return value
    return f"{order_date.strftime('%B')} {order_date.day}, {order_date.year}"


def generate_packing_slip_pdf(order: dict, config: dict) -> Path:
    template_dir = Path(__file__).resolve().parents[1] / "config"
    template = Environment(loader=FileSystemLoader(str(template_dir))).get_template("packing_slip.html")
    rendered_html = template.render(
        order=order,
        order_number=order.get("name", "").lstrip("#"),
        order_date=_format_order_date(order.get("created_at", "")),
        company=config.get("company", {
            "name": "Customer Order",
            "address_line1": "",
            "city_state_zip": "",
            "email": "",
            "website": "",
        }),
    )

    output_dir = Path(config["packing_slip_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    order_number = order["name"].replace("#", "")
    output_path = output_dir / f"PackingSlip_{order_number}.pdf"

    HTML(string=rendered_html, base_url=str(template_dir)).write_pdf(output_path)

    return output_path