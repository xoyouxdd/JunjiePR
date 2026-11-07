"""Fill the reviewed Artifact Tool template without adding a Node runtime dependency."""
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
ET.register_namespace('', NS)


def tag(name):
    return '{'+NS+'}'+name


def cell(address, value, style=None):
    element = ET.Element(tag('c'), {'r': address})
    if style is not None:
        element.set('s', style)
    if value is None:
        return element
    if isinstance(value, bool):
        element.set('t', 'b')
        ET.SubElement(element, tag('v')).text = '1' if value else '0'
    elif isinstance(value, int):
        ET.SubElement(element, tag('v')).text = str(value)
    else:
        element.set('t', 'inlineStr')
        text = ET.SubElement(ET.SubElement(element, tag('is')), tag('t'))
        text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        text.text = str(value)
    return element


def workbook(batch, rows):
    template = Path(__file__).resolve().parents[1] / 'templates' / 'legacy-points.xlsx'
    with ZipFile(template) as source:
        files = {name: source.read(name) for name in source.namelist()}
    sheet = ET.fromstring(files['xl/worksheets/sheet1.xml'])
    data = sheet.find(tag('sheetData'))
    styles = {c.attrib['r'].rstrip('0123456789'): c.attrib.get('s') for c in data.find(f"{tag('row')}[@r='4']")}
    for old in list(data):
        if int(old.attrib['r']) >= 4:
            data.remove(old)
    for number, row in enumerate(rows, 4):
        line = ET.SubElement(data, tag('row'), {'r': str(number), 'ht': '48', 'customHeight': '1'})
        values = [row['employee_no'], row['name'], row['circle'], row['role'], row['status'], row['cutoff'],
                  row['total'], None, None, row['coverage']]
        for col, value in zip('ABCDEFGHIJ', values):
            line.append(cell(col+str(number), value, styles.get(col)))
    end = max(4, len(rows)+3)
    dimension = sheet.find(tag('dimension'))
    if dimension is None:
        dimension = ET.Element(tag('dimension'))
        sheet.insert(1 if sheet.find(tag('sheetPr')) is not None else 0, dimension)
    dimension.set('ref', f'A1:J{end}')
    for validation in sheet.findall(f"{tag('dataValidations')}/{tag('dataValidation')}"):
        validation.set('sqref', f'H4:H{end}')
    filt = ET.Element(tag('autoFilter'), {'ref': f'A3:J{end}'})
    validation = sheet.find(tag('dataValidations'))
    sheet.insert(list(sheet).index(validation) if validation is not None else len(sheet), filt)
    files['xl/worksheets/sheet1.xml'] = ET.tostring(sheet, encoding='utf-8', xml_declaration=True)
    meta = ET.fromstring(files['xl/worksheets/sheet2.xml'])
    meta_data = meta.find(tag('sheetData'));meta_data.clear()
    line = ET.SubElement(meta_data, tag('row'), {'r': '1'})
    line.append(cell('A1', batch.id));line.append(cell('B1', 'legacy-points-v1'))
    files['xl/worksheets/sheet2.xml'] = ET.tostring(meta, encoding='utf-8', xml_declaration=True)
    book = ET.fromstring(files['xl/workbook.xml'])
    for entry in book.find(tag('sheets')):
        if entry.attrib['name'] == '_control':
            entry.set('state', 'hidden')
    files['xl/workbook.xml'] = ET.tostring(book, encoding='utf-8', xml_declaration=True)
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as target:
        for name, content in files.items():
            target.writestr(name, content)
    return output.getvalue()
