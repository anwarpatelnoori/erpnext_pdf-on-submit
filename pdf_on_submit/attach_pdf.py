import frappe
from frappe import _
from frappe.core.api.file import create_new_folder
from frappe.model.naming import _format_autoname
from frappe.realtime import publish_realtime
from frappe.utils.weasyprint import PrintFormatGenerator


def attach_pdf(doc, event=None):
    settings = frappe.get_single("PDF on Submit Settings")

    if enabled_doctypes := settings.get("enabled_for", {"document_type": doc.doctype}):
        enabled_doctype = enabled_doctypes[0]
    else:
        return

    auto_name = enabled_doctype.auto_name
    print_format = (
        enabled_doctype.print_format or doc.meta.default_print_format or "Standard"
    )
    letter_head = enabled_doctype.letter_head or None

    fallback_language = (
        frappe.db.get_single_value("System Settings", "language") or "en"
    )
    args = {
        "doctype": doc.doctype,
        "name": doc.name,
        "title": doc.get_title() if doc.meta.title_field else None,
        "lang": getattr(doc, "language", fallback_language),
        "show_progress": not settings.create_pdf_in_background,
        "auto_name": auto_name,
        "print_format": print_format,
        "letter_head": letter_head,
        "custom_attach_field": "attach_your_contract" if doc.doctype == "Contract Letter" else None,
    }

    frappe.enqueue(
        method=execute,
        timeout=30,
        now=bool(
            not settings.create_pdf_in_background
            or frappe.flags.in_test
            or frappe.conf.developer_mode
        ),
        **args,
    )


def execute(
    doctype,
    name,
    title=None,
    lang=None,
    show_progress=True,
    auto_name=None,
    print_format=None,
    letter_head=None,
    custom_attach_field=None,
):
    def publish_progress(percent):
        publish_realtime(
            "progress",
            {"percent": percent, "title": _("Uploading Your Contract Please Wait:) "), "description": None},
            doctype=doctype,
            docname=name,
        )

    if lang:
        frappe.local.lang = lang
        frappe.local.lang_full_dict = None
        frappe.local.jenv = None

    if show_progress:
        publish_progress(0)

    doctype_folder = create_folder(doctype, "Home")
    title_folder = create_folder(title, doctype_folder) if title else None
    target_folder = title_folder or doctype_folder

    if show_progress:
        publish_progress(33)

    if frappe.db.get_value("Print Format", print_format, "print_format_builder_beta"):
        doc = frappe.get_doc(doctype, name)
        pdf_data = PrintFormatGenerator(print_format, doc, letter_head).render_pdf()
    else:
        pdf_data = get_pdf_data(doctype, name, print_format, letter_head)

    if show_progress:
        publish_progress(66)

    save_and_attach(pdf_data, doctype, name, target_folder, auto_name, custom_attach_field)

    if show_progress:
        publish_progress(100)


def create_folder(folder, parent):
    new_folder_name = "/".join([parent, folder])

    if not frappe.db.exists("File", new_folder_name):
        create_new_folder(folder, parent)

    return new_folder_name


def get_pdf_data(doctype, name, print_format=None, letterhead=None):
    html = frappe.get_print(doctype, name, print_format, letterhead=letterhead)
    return frappe.utils.pdf.get_pdf(html)


def save_and_attach(content, to_doctype, to_name, folder, auto_name=None, custom_attach_field=None):
    if auto_name:
        doc = frappe.get_doc(to_doctype, to_name)
        pdf_name = set_name_from_naming_options(auto_name, doc)
        file_name = "{pdf_name}.pdf".format(pdf_name=pdf_name.replace("/", "-"))
    else:
        file_name = "{}.pdf".format(to_name.replace("/", "-"))

    file = frappe.new_doc("File")
    file.file_name = file_name
    file.content = content
    file.folder = folder
    file.is_private = 1
    file.attached_to_doctype = to_doctype
    file.attached_to_name = to_name
    file.save()

    if custom_attach_field and to_doctype == "Contract Letter":
        doc = frappe.get_doc(to_doctype, to_name)
        doc.db_set(custom_attach_field, file.file_url)

    # Trigger a real-time event to notify the client-side script
    frappe.realtime.publish_realtime('pdf_attached', {'docname': to_name})


def set_name_from_naming_options(autoname, doc):
    _autoname = autoname.lower()

    if _autoname.startswith("format:"):
        return _format_autoname(autoname, doc)

    return doc.name
