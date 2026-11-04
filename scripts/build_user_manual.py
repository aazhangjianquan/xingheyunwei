"""Build the user-facing Word manual from its maintained Markdown source."""
from pathlib import Path
import re

from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs' / '星河项目协作平台使用手册.md'
OUTPUT = SOURCE.with_suffix('.docx')
doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Inches(8.5), Inches(11)
sec.top_margin, sec.bottom_margin = Inches(.68), Inches(.65)
sec.left_margin, sec.right_margin = Inches(.8), Inches(.8)
sec.header_distance, sec.footer_distance = Inches(.28), Inches(.28)
grid = sec._sectPr.find(qn('w:docGrid'))
if grid is not None:
    sec._sectPr.remove(grid)
WIDTH = 6.9


def font(style, size, bold=False, east='Songti SC'):
    style.font.name = 'Arial'
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.italic = False
    style.font.color.rgb = RGBColor(0, 0, 0)
    rf = style.element.get_or_add_rPr().get_or_add_rFonts()
    for attr in list(rf.attrib):
        if 'theme' in attr.lower():
            del rf.attrib[attr]
    for attr in ('ascii', 'hAnsi', 'cs'):
        rf.set(qn('w:' + attr), 'Arial')
    rf.set(qn('w:eastAsia'), east)
    rf.set(qn('w:hint'), 'eastAsia')
    pr = style.element.get_or_add_rPr()
    for tag, val in [('szCs', str(round(size * 2))), ('iCs', '0')]:
        el = pr.find(qn('w:' + tag))
        if el is None:
            el = OxmlElement('w:' + tag)
            pr.append(el)
        el.set(qn('w:val'), val)
    pp = style.element.get_or_add_pPr()
    snap = OxmlElement('w:snapToGrid')
    snap.set(qn('w:val'), '0')
    pp.append(snap)


font(doc.styles['Normal'], 11)
normal = doc.styles['Normal'].paragraph_format
normal.line_spacing = Pt(15.5)
normal.space_after = Pt(6)
normal.widow_control = True
for name, size, before, after in [
    ('Title', 25, 0, 14), ('Subtitle', 9.5, 0, 12),
    ('Heading 1', 18, 0, 13), ('Heading 2', 12, 10, 5),
]:
    st = doc.styles[name]
    font(st, size, name != 'Subtitle', 'Heiti SC')
    st.paragraph_format.space_before = Pt(before)
    st.paragraph_format.space_after = Pt(after)
    st.paragraph_format.line_spacing = Pt(size * 1.3)
    st.paragraph_format.keep_with_next = name != 'Subtitle'
    st.paragraph_format.keep_together = True
    borders = st.element.get_or_add_pPr().find(qn('w:pBdr'))
    if borders is not None:
        borders.getparent().remove(borders)
for name in ('Header', 'Footer'):
    font(doc.styles[name], 8, east='Heiti SC')
    doc.styles[name].paragraph_format.space_after = Pt(0)

head = sec.header.paragraphs[0]
head.text = '星河项目协作平台                                      产品使用手册'
foot = sec.footer.paragraphs[0]
foot.alignment = WD_ALIGN_PARAGRAPH.RIGHT
foot.add_run('第 ')
field = OxmlElement('w:fldSimple')
field.set(qn('w:instr'), 'PAGE')
foot._p.append(field)
foot.add_run(' 页')

numbering = doc.part.numbering_part.element
next_num = 80


def add_numbering(bullet):
    global next_num
    ident = next_num
    next_num += 1
    abstract = OxmlElement('w:abstractNum')
    abstract.set(qn('w:abstractNumId'), str(ident))
    multi = OxmlElement('w:multiLevelType')
    multi.set(qn('w:val'), 'singleLevel')
    abstract.append(multi)
    lvl = OxmlElement('w:lvl')
    lvl.set(qn('w:ilvl'), '0')
    for tag, val in [('start', '1'), ('numFmt', 'bullet' if bullet else 'decimal'),
                     ('lvlText', '•' if bullet else '%1.'), ('lvlJc', 'left')]:
        el = OxmlElement('w:' + tag)
        el.set(qn('w:val'), val)
        lvl.append(el)
    pp = OxmlElement('w:pPr')
    indent = OxmlElement('w:ind')
    indent.set(qn('w:left'), '340')
    indent.set(qn('w:hanging'), '300')
    pp.append(indent)
    lvl.append(pp)
    abstract.append(lvl)
    numbering.append(abstract)
    num = OxmlElement('w:num')
    num.set(qn('w:numId'), str(ident))
    aid = OxmlElement('w:abstractNumId')
    aid.set(qn('w:val'), str(ident))
    num.append(aid)
    numbering.append(num)
    return ident


def rich(p, text):
    for i, chunk in enumerate(re.split(r'\*\*(.*?)\*\*', text)):
        run = p.add_run(chunk)
        if i % 2:
            run.bold = True


def table(lines):
    rows = [[c.strip() for c in line.strip().strip('|').split('|')] for line in lines]
    rows.pop(1)
    count = len(rows[0])
    if count == 2:
        widths = [2.0, 4.9]
    elif count == 3:
        widths = [1.65, 1.9, 3.35]
    else:
        widths = [1.28, 1.22, 1.1, 1.85, 1.45]
    t = doc.add_table(rows=0, cols=count)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for col, width in zip(t.columns, widths):
        col.width = Inches(width)
    pr = t._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el = OxmlElement('w:' + edge)
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '4')
        el.set(qn('w:color'), 'D9D9D9')
        borders.append(el)
    pr.append(borders)
    margins = OxmlElement('w:tblCellMar')
    for edge, val in [('top', '90'), ('bottom', '90'), ('left', '105'), ('right', '105')]:
        el = OxmlElement('w:' + edge)
        el.set(qn('w:w'), val)
        el.set(qn('w:type'), 'dxa')
        margins.append(el)
    pr.append(margins)
    for ri, values in enumerate(rows):
        row = t.add_row()
        rp = row._tr.get_or_add_trPr()
        rp.append(OxmlElement('w:cantSplit'))
        if ri == 0:
            rp.append(OxmlElement('w:tblHeader'))
        for ci, (cell, value, width) in enumerate(zip(row.cells, values, widths)):
            cell.width = Inches(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(13)
            if count == 5:
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            rich(p, value)
            for run in p.runs:
                run.font.size = Pt(9.6)
                if ri == 0:
                    run.bold = True
            if ri == 0:
                shade = OxmlElement('w:shd')
                shade.set(qn('w:fill'), 'EAF0F5')
                cell._tc.get_or_add_tcPr().append(shade)
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_after = Pt(0)
    spacer.paragraph_format.line_spacing = 1
    spacer.paragraph_format.keep_with_next = True
    spacer.add_run().font.size = Pt(3)


lines = SOURCE.read_text().splitlines()
i = 0
previous_list = None
num_id = None
while i < len(lines):
    line = lines[i].strip()
    if not line:
        previous_list = None
        i += 1
        continue
    if line == '<!-- pagebreak -->':
        doc.add_page_break()
    elif line.startswith('|'):
        batch = []
        while i < len(lines) and lines[i].strip().startswith('|'):
            batch.append(lines[i])
            i += 1
        table(batch)
        continue
    elif line.startswith('# '):
        rich(doc.add_paragraph(style='Title'), line[2:])
    elif line.startswith('## '):
        p = doc.add_paragraph(style='Heading 1')
        rich(p, line[3:])
        bookmark = OxmlElement('w:bookmarkStart')
        bookmark.set(qn('w:id'), str(i))
        bookmark.set(qn('w:name'), 'section_' + str(i))
        p._p.insert(0, bookmark)
        end = OxmlElement('w:bookmarkEnd')
        end.set(qn('w:id'), str(i))
        p._p.append(end)
    elif line.startswith('### '):
        rich(doc.add_paragraph(style='Heading 2'), line[4:])
    elif line.startswith('编写日期 '):
        rich(doc.add_paragraph(style='Subtitle'), line)
    elif line.startswith('- ') or re.match(r'^\d+\. ', line):
        kind = 'bullet' if line.startswith('- ') else 'decimal'
        if kind != previous_list:
            num_id = add_numbering(kind == 'bullet')
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(3)
        npr = p._p.get_or_add_pPr().get_or_add_numPr()
        npr.get_or_add_ilvl().val = 0
        npr.get_or_add_numId().val = num_id
        rich(p, re.sub(r'^(?:- |\d+\. )', '', line))
        previous_list = kind
        i += 1
        continue
    else:
        p = doc.add_paragraph()
        rich(p, line)
    previous_list = None
    i += 1

doc.core_properties.title = '星河项目协作平台使用手册'
doc.core_properties.subject = '成员操作 审批执行 知识协作与管理员配置'
doc.core_properties.author = '星河项目协作平台'
doc.core_properties.keywords = '使用手册 项目管理 审批 知识库'
doc.core_properties.comments = ''
doc.save(OUTPUT)
print(OUTPUT)
