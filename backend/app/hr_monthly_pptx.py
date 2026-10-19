"""Fill approved OOXML templates without a desktop Office/runtime dependency.

Native DrawingML tables and charts remain editable, including chart workbooks.
Only parts reachable from the final presentation survive (no hidden sample data).
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import posixpath
import zipfile
import xml.etree.ElementTree as ET


NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main", "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships", "c": "http://schemas.openxmlformats.org/drawingml/2006/chart", "rel": "http://schemas.openxmlformats.org/package/2006/relationships", "ct": "http://schemas.openxmlformats.org/package/2006/content-types"}
for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)
ROOT = Path(__file__).parent / "report_templates" / "hr_monthly"


def tag(prefix, name):
    return f"{{{NS[prefix]}}}{name}"


def xml(root):
    value = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    # OPC's package readers expect unprefixed Relationships/Types roots.
    for prefix in ("ct", "rel"):
        if root.tag.startswith("{" + NS[prefix] + "}"):
            value = value.replace(f"xmlns:{prefix}=".encode(), b"xmlns=").replace(f"<{prefix}:".encode(), b"<").replace(f"</{prefix}:".encode(), b"</")
    return value


def set_text(node, text):
    if "\n" in str(text):
        body = node.find("p:txBody", NS)
        if body is None:
            body = node.find("a:txBody", NS)
        if body is not None:
            paragraphs = body.findall("a:p", NS)
            if paragraphs:
                prototype = deepcopy(paragraphs[0])
                for paragraph in paragraphs:
                    body.remove(paragraph)
                for line in str(text).splitlines():
                    paragraph = deepcopy(prototype)
                    set_text(paragraph, line)
                    body.append(paragraph)
                return
    values = node.findall(".//a:t", NS)
    if values:
        values[0].text = str(text)
        for t in values[1:]:
            t.text = ""
TEMPLATE_VERSION = "2"


def pack_report(parts, slides, overrides, content_types):
    presentation = ET.fromstring(parts["ppt/presentation.xml"])
    ids = presentation.find("p:sldIdLst", NS)
    ids.clear()
    rels = ET.fromstring(parts["ppt/_rels/presentation.xml.rels"])
    for rel in list(rels):
        if rel.get("Type", "").endswith("/slide"):
            rels.remove(rel)
    for i, (slide, slide_rels) in enumerate(slides, 1):
        name = f"ppt/slides/report{i}.xml"
        parts[name] = xml(slide)
        parts[f"ppt/slides/_rels/report{i}.xml.rels"] = xml(slide_rels)
        overrides[name] = "application/vnd.openxmlformats-officedocument.presentationml.slide+xml"
        rid = f"hrSlide{i}"
        ET.SubElement(ids, tag("p", "sldId"), {"id": str(256 + i), tag("r", "id"): rid})
        ET.SubElement(rels, tag("rel", "Relationship"), Id=rid, Type=NS["r"] + "/slide", Target="/" + name)
    parts["ppt/presentation.xml"] = xml(presentation)
    parts["ppt/_rels/presentation.xml.rels"] = xml(rels)
    if "docProps/app.xml" in parts:
        properties = ET.fromstring(parts["docProps/app.xml"])
        for node in properties.iter():
            if node.tag.endswith("}Slides"):
                node.text = str(len(slides))
        parts["docProps/app.xml"] = xml(properties)
    # Export no template sample text, embedded sample workbooks, or unused slides.
    reachable = set()
    def visit(name):
        if name in reachable or name not in parts:
            return
        reachable.add(name)
        relpath = posixpath.join(posixpath.dirname(name), "_rels", posixpath.basename(name) + ".rels") if name else "_rels/.rels"
        if relpath in parts:
            reachable.add(relpath)
            for rel in ET.fromstring(parts[relpath]):
                if rel.get("TargetMode") != "External":
                    target = rel.get("Target")
                    visit(target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(name), target)))
    reachable.add("_rels/.rels")
    for rel in ET.fromstring(parts["_rels/.rels"]):
        visit(rel.get("Target").lstrip("/"))
    for child in list(content_types):
        if child.tag == tag("ct", "Override"):
            content_types.remove(child)
    for path in sorted(reachable):
        if path in overrides:
            ET.SubElement(content_types, tag("ct", "Override"), PartName="/" + path, ContentType=overrides[path])
    parts["[Content_Types].xml"] = xml(content_types)
    reachable.add("[Content_Types].xml")
    result = BytesIO()
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as output:
        for path in sorted(reachable):
            output.writestr(path, parts[path])
    return result.getvalue()


def build_pptx(data, template, sections, excellent_ids, notes, birthday, photos, photo_caption, label):
    from app.hr_monthly_layouts import export_report
    return export_report(data, template, sections, excellent_ids, notes, birthday, photos, photo_caption, label)
