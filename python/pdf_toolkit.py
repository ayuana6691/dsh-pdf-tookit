#!/usr/bin/env python3
"""Portable PDF operations. One JSON request on stdin, one JSON result on stdout."""
from __future__ import annotations
import csv
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


class PDFError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise PDFError(message)


def path(value):
    need(isinstance(value, str) and value.strip(), 'A non-empty file path is required')
    return Path(value).expanduser().resolve()


def pages(spec, count, repeat=False):
    """Public page numbers are 1-based; order is preserved."""
    if spec is None or spec == 'all':
        return list(range(count))
    values = []
    for part in str(spec).split(','):
        part = part.strip()
        need(bool(re.fullmatch(r'\d+(?:-\d+)?', part)), f'Invalid page selector: {part}')
        ends = list(map(int, part.split('-')))
        start, end = ends[0], ends[-1]
        values.extend(range(start, end + (1 if end >= start else -1), 1 if end >= start else -1))
    need(values and all(1 <= v <= count for v in values), f'Pages must be between 1 and {count}')
    need(repeat or len(set(values)) == len(values), 'Duplicate pages are not allowed for this operation')
    return [v - 1 for v in values]


def number(value, name, low=None, high=None):
    need(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value), f'{name} must be finite')
    need(low is None or value >= low, f'{name} must be >= {low}')
    need(high is None or value <= high, f'{name} must be <= {high}')
    return value


def rect(value):
    import pymupdf as fitz
    need(isinstance(value, list) and len(value) == 4, 'rect must be [x0,y0,x1,y1] in points')
    for v in value:
        number(v, 'rect coordinate')
    r = fitz.Rect(value)
    need(not r.is_empty and not r.is_infinite, 'rect must have positive width and height')
    return r


def open_pdf(options, key='input'):
    import pymupdf as fitz
    p = path(options.get(key))
    doc = fitz.open(p)
    need(doc.is_pdf, f'Not a PDF: {p}')
    if doc.needs_pass:
        need(bool(doc.authenticate(options.get('password', ''))), 'PDF password is missing or incorrect')
    return doc


def reader(options):
    from pypdf import PdfReader
    r = PdfReader(path(options.get('input')))
    if r.is_encrypted:
        need(bool(r.decrypt(options.get('password', ''))), 'PDF password is missing or incorrect')
    return r


def destination(options, inputs=()):
    p = path(options.get('output'))
    need(p.suffix.lower() == '.pdf', 'output must end in .pdf')
    need(all(p != path(v) for v in inputs), 'Use a new output path; source PDF is never overwritten')
    need(not p.exists(), f'Output already exists: {p}')
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def commit(producer, output, password='', expected_pages=None):
    """Validate in a sibling temporary file, then publish without clobbering."""
    import pymupdf as fitz
    fd, temp = tempfile.mkstemp(prefix='.pdf-toolkit-', suffix='.pdf', dir=output.parent)
    os.close(fd)
    try:
        producer(temp)
        with fitz.open(temp) as result:
            if result.needs_pass:
                need(bool(result.authenticate(password)), 'Could not reopen encrypted output')
            need(len(result) > 0, 'Output has no pages')
            need(expected_pages is None or len(result) == expected_pages, 'Output page count mismatch')
            # Exercise every output page, including appearance streams, at low resolution.
            for page in result:
                page.get_pixmap(matrix=fitz.Matrix(0.3, 0.3), alpha=False)
            count = len(result)
        os.link(temp, output)  # fails if another writer created the destination
        return {'output': str(output), 'pages': count, 'bytes': output.stat().st_size,
                'sha256': sha256(output), 'validation': 'reopened and rendered every page'}
    finally:
        Path(temp).unlink(missing_ok=True)


def save_doc(doc, options, extra=None):
    out = destination(options, [options['input']] if 'input' in options else [])
    if doc.is_encrypted and options.get('action') not in ('decrypt', 'encrypt'):
        raise PDFError('Decrypt to a separate copy before editing an encrypted PDF')
    result = commit(lambda p: doc.save(p, garbage=4, deflate=True), out, expected_pages=len(doc))
    result.update(extra or {})
    return result


def inspect_pdf(o):
    with open_pdf(o) as doc:
        selected = pages(o.get('pages'), len(doc))
        result = {'input': str(path(o['input'])), 'pages': len(doc), 'metadata': doc.metadata,
                  'encrypted': bool(doc.metadata.get('encryption')), 'page_details': []}
        for i in selected:
            p = doc[i]
            result['page_details'].append({'page': i + 1, 'size': list(p.rect), 'rotation': p.rotation,
                'text_characters': len(p.get_text()), 'images': len(p.get_images()),
                'fonts': [{'xref': f[0], 'type': f[2], 'name': f[3], 'resource': f[4]} for f in p.get_fonts()],
                'widgets': [{'name': w.field_name, 'type': w.field_type_string, 'value': w.field_value,
                             'rect': list(w.rect)} for w in (p.widgets() or [])]})
        return result


def new_directory(value):
    p = path(value)
    need(not p.exists(), f'Output directory already exists: {p}')
    p.mkdir(parents=True)
    return p


def extract(o):
    kind = o.get('kind', 'text')
    need(kind in ('text', 'tables', 'images'), 'kind must be text, tables or images')
    with open_pdf(o) as doc:
        indices = pages(o.get('pages'), len(doc))
        folder = new_directory(o.get('output_dir'))
        files = []
        if kind == 'text':
            records = [{'page': i + 1, 'text': doc[i].get_text(sort=True)} for i in indices]
            f = folder / 'text.txt'
            f.write_text('\n\f\n'.join(x['text'] for x in records), encoding='utf-8')
            j = folder / 'text.json'
            j.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
            files.extend([str(f), str(j)])
        elif kind == 'images':
            seen = set()
            for i in indices:
                for item in doc[i].get_images(full=True):
                    xref = item[0]
                    if xref in seen:
                        continue
                    seen.add(xref)
                    im = doc.extract_image(xref)
                    f = folder / f'page-{i+1:04d}-xref-{xref}.{im["ext"]}'
                    f.write_bytes(im['image'])
                    files.append(str(f))
        else:
            import pdfplumber
            records = []
            with pdfplumber.open(o['input'], password=o.get('password')) as pdf:
                for i in indices:
                    for n, table in enumerate(pdf.pages[i].extract_tables(o.get('table_settings', {})), 1):
                        records.append({'page': i+1, 'table': n, 'rows': table})
                        f = folder / f'page-{i+1:04d}-table-{n}.csv'
                        with f.open('w', newline='', encoding='utf-8-sig') as stream:
                            csv.writer(stream).writerows(table)
                        files.append(str(f))
            f = folder / 'tables.json'
            f.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
            files.append(str(f))
        return {'kind': kind, 'files': files, 'pages': [i+1 for i in indices],
                'note': 'Image extraction returns embedded image resources; use render for complete pages.' if kind == 'images' else ''}


def render(o):
    import pymupdf as fitz
    dpi = number(o.get('dpi', 144), 'dpi', 36, 600)
    with open_pdf(o) as doc:
        indices = pages(o.get('pages'), len(doc))
        folder = new_directory(o.get('output_dir'))
        files = []
        for i in indices:
            p = doc[i]
            need(p.rect.width * p.rect.height * (dpi/72)**2 <= 100_000_000, 'Rendered page exceeds 100 million pixels; reduce dpi')
            f = folder / f'page-{i+1:04d}.png'
            p.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False).save(f)
            files.append(str(f))
        return {'files': files, 'dpi': dpi, 'pages': [i+1 for i in indices]}


def compare(o):
    import pymupdf as fitz
    from PIL import Image, ImageChops, ImageDraw
    dpi = number(o.get('dpi', 120), 'dpi', 36, 300)
    ignored = o.get('ignore_regions', [])
    need(isinstance(ignored, list), 'ignore_regions must be an array')
    with open_pdf(o, 'before') as a, open_pdf(o, 'after') as b:
        need(len(a) == len(b), 'Comparison requires equal page counts')
        indices = pages(o.get('pages'), len(a))
        folder = new_directory(o['output_dir']) if o.get('output_dir') else None
        results = []
        for i in indices:
            need(a[i].rect == b[i].rect, f'Page {i+1}: dimensions differ')
            need(a[i].rect.width*a[i].rect.height*(dpi/72)**2 <= 100_000_000, 'Comparison page too large; reduce dpi')
            pixmaps = [p.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False) for p in (a[i], b[i])]
            ims = [Image.frombytes('RGB', [p.width, p.height], p.samples) for p in pixmaps]
            diff = ImageChops.difference(*ims)
            raw_bbox = diff.getbbox()
            mask = Image.new('L', ims[0].size, 255)
            draw = ImageDraw.Draw(mask)
            for region in ignored:
                need(isinstance(region, dict) and 'page' in region and 'rect' in region, 'Each ignored region needs page and rect')
                if region['page'] == i+1:
                    r = rect(region['rect'])
                    # compare coordinates are rendered (rotated) page coordinates.
                    draw.rectangle([math.floor(r.x0*dpi/72), math.floor(r.y0*dpi/72),
                                    math.ceil(r.x1*dpi/72), math.ceil(r.y1*dpi/72)], fill=0)
            remaining = ImageChops.multiply(diff, mask.convert('RGB'))
            channels = remaining.split()
            nonzero = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
            changed = nonzero.width * nonzero.height - nonzero.histogram()[0]
            item = {'page': i+1, 'changed_pixels_outside_regions': changed,
                    'difference_bbox_pixels': raw_bbox, 'outside_bbox_pixels': remaining.getbbox()}
            if folder:
                f = folder / f'difference-{i+1:04d}.png'
                diff.save(f)
                item['difference_image'] = str(f)
            results.append(item)
        return {'identical_outside_regions': all(x['changed_pixels_outside_regions'] == 0 for x in results),
                'pages': results, 'dpi': dpi, 'ignored_regions': ignored,
                'note': 'Pixel equality is measured at this dpi, not proof of PDF object or semantic equality.'}


def pypdf_transform(action, o):
    from pypdf import PdfWriter
    inputs = o.get('inputs') if action == 'merge' else [o.get('input')]
    need(isinstance(inputs, list) and inputs and all(isinstance(v, str) for v in inputs), 'inputs must be a non-empty list of PDF paths')
    out = destination(o, inputs)
    w = PdfWriter()
    if action == 'merge':
        for n, f in enumerate(inputs):
            r = reader({'input': f, 'password': o.get('passwords', {}).get(f, '')})
            if r.get_fields():
                r.add_form_topname(f'document{n+1}')
            w.append(r)
    else:
        r = reader(o)
        if action == 'select':
            w.append(r, pages=pages(o.get('pages'), len(r.pages), repeat=True))
        else:
            w.clone_document_from_reader(r)
            if action == 'rotate':
                angle = o.get('angle', 90)
                need(isinstance(angle, int) and angle % 90 == 0, 'angle must be a multiple of 90')
                for i in pages(o.get('pages'), len(w.pages)):
                    w.pages[i].rotate(angle)
            elif action == 'metadata':
                data = o.get('metadata')
                need(isinstance(data, dict) and all(isinstance(k,str) and isinstance(v,str) for k,v in data.items()), 'metadata must map string keys to string values')
                w.add_metadata({k if k.startswith('/') else '/'+k: v for k,v in data.items()})
            elif action == 'encrypt':
                need(bool(o.get('user_password')), 'user_password must be non-empty')
                w.encrypt(o['user_password'], owner_password=o.get('owner_password'), algorithm='AES-256')
            elif action == 'compress':
                for p in w.pages:
                    p.compress_content_streams()
                w.compress_identical_objects(remove_duplicates=True, remove_unreferenced=True)
            elif action != 'decrypt':
                raise PDFError(f'Unknown transform: {action}')
    result = commit(lambda p: w.write(p), out, o.get('user_password', ''), expected_pages=len(w.pages))
    if action == 'compress':
        result['original_bytes'] = path(o['input']).stat().st_size
        result['saved_bytes'] = result['original_bytes'] - result['bytes']
    return result


def split(o):
    from pypdf import PdfWriter
    r = reader(o)
    groups = o.get('groups', [str(i+1) for i in range(len(r.pages))])
    need(isinstance(groups, list) and groups, 'groups must be a non-empty array of page selectors')
    selections = [pages(g, len(r.pages), repeat=True) for g in groups]
    folder = new_directory(o.get('output_dir'))
    outputs = []
    for n, selection in enumerate(selections, 1):
        w = PdfWriter()
        w.append(r, pages=selection)
        out = folder / f'part-{n:04d}.pdf'
        outputs.append(commit(lambda p: w.write(p), out, expected_pages=len(selection)))
    return {'outputs': outputs}


def fill(o):
    from pypdf import PdfWriter, PdfReader
    from pypdf.generic import NameObject
    r = reader(o)
    need(not r.trailer['/Root'].get('/AcroForm', {}).get('/XFA'), 'XFA forms are not supported')
    values = o.get('values')
    need(isinstance(values, dict) and values, 'values must be a non-empty field/value mapping')
    fields = r.get_fields() or {}
    need(not (set(values)-set(fields)), f'Unknown fields: {sorted(set(values)-set(fields))}')
    need(all(fields[k].get('/FT') != '/Sig' for k in values), 'Signature fields cannot be filled as text')
    need(all(isinstance(v,str) for v in values.values()), 'Field values must be strings; checkbox values use their exact appearance state, e.g. /Yes')
    for k,v in values.items():
        states=fields[k].get('/_States_')
        if states:
            need(v in states, f'{k}: expected one of {states}')
    w = PdfWriter(); w.clone_document_from_reader(r)
    flatten = bool(o.get('flatten', False))
    if flatten:
        need(not any(f.get('/FT') == '/Sig' and f.get('/V') for f in fields.values()), 'Flattening a signed PDF is not supported')
    actual = {k: f.get('/V', '/Off' if f.get('/FT') == '/Btn' else '') for k,f in fields.items()} if flatten else {}
    actual.update(values)
    w.update_page_form_field_values(None, actual, auto_regenerate=False, flatten=flatten)
    if flatten:
        w.remove_annotations(subtypes='/Widget')
        w.root_object.pop(NameObject('/AcroForm'), None)
    out = destination(o, [o['input']])
    def produce(p):
        w.write(p)
        rr = PdfReader(p)
        ff = rr.get_fields() or {}
        if flatten:
            need(not ff, 'Flattened output still has fields')
        else:
            for k,v in values.items():
                need(str(ff[k].get('/V','')) == v, f'Canonical field mismatch: {k}')
        for pp in rr.pages:
            for a in pp.get('/Annots', []):
                widget = a.get_object()
                if widget.get('/Subtype') != '/Widget':
                    continue
                need(not flatten, 'Flattened output still contains widgets')
                chain=[]; node=widget
                while node:
                    chain.append(node)
                    node=node.get('/Parent')
                    node=node.get_object() if node else None
                key='.'.join(str(x['/T']) for x in reversed(chain) if '/T' in x)
                if key in values:
                    value=next((x['/V'] for x in chain if '/V' in x), '')
                    need(str(value)==values[key], f'Widget field mismatch: {key}')
                    need('/N' in widget.get('/AP', {}), f'Missing appearance: {key}')
    return commit(produce, out, expected_pages=len(w.pages))


def resolve_font(doc, page, span, new_text, override=None):
    import pymupdf as fitz
    if override:
        data = path(override).read_bytes()
        font = fitz.Font(fontbuffer=data)
        need(all(font.has_glyph(ord(c)) for c in new_text), 'Provided font lacks replacement characters')
        return font, data, None
    normalized = lambda s: re.sub(r'^[A-Z]{6}\+', '', s).replace(' ', '').lower()
    for f in page.get_fonts(full=True):
        if normalized(f[3]) == normalized(span['font']):
            _, _, _, data = doc.extract_font(f[0])
            if data:
                font = fitz.Font(fontbuffer=data)
                if all(font.has_glyph(ord(c)) for c in new_text):
                    return font, data, None
    base = {'Helvetica':'helv','Helvetica-Bold':'hebo','Helvetica-Oblique':'heit','Helvetica-BoldOblique':'hebi',
            'Times-Roman':'tiro','Times-Bold':'tibo','Times-Italic':'tiit','Times-BoldItalic':'tibi',
            'Courier':'cour','Courier-Bold':'cobo','Courier-Oblique':'coit','Courier-BoldOblique':'cobi'}
    if span['font'] in base:
        font = fitz.Font(fontname=base[span['font']])
        need(all(ord(c)<256 and font.has_glyph(ord(c)) for c in new_text), 'Base-14 replacement needs an explicit Unicode font_file')
        return font, None, base[span['font']]
    raise PDFError(f'Cannot reuse font {span["font"]!r} for all new characters; provide font_file explicitly')


def replace(o):
    """General text-layer replacement: preflight every hit, redact text, reuse font."""
    import pymupdf as fitz
    old, new = o.get('old'), o.get('new')
    need(isinstance(old,str) and old and isinstance(new,str) and new and old != new, 'old/new must be non-empty different strings')
    need('\n' not in old+new, 'Replacement is single-line only')
    fit = o.get('fit', 'error'); align = o.get('align','left')
    need(fit in ('error','expand','shrink') and align in ('left','right'), 'fit: error/expand/shrink; align: left/right')
    with open_pdf(o) as doc:
        planned=[]; expected=0; original_old={}; original_new={}
        indices=pages(o.get('pages'),len(doc))
        for i in indices:
            p=doc[i]; original_old[i]=p.get_text().count(old); original_new[i]=p.get_text().count(new); expected+=original_old[i]
            need(not any(a.type[0]==fitz.PDF_ANNOT_REDACT for a in (p.annots() or [])), 'Page already has redaction annotations; apply or remove them separately')
            for block in p.get_text('rawdict')['blocks']:
                for line in block.get('lines',[]):
                    chars=[(c,s) for s in line['spans'] for c in s['chars']]
                    text=''.join(c['c'] for c,s in chars)
                    for m in re.finditer(re.escape(old),text):
                        need(tuple(line['dir']) == (1.0,0.0), 'Rotated/vertical text replacement is not supported')
                        hit=chars[m.start():m.end()]; span=hit[0][1]
                        need(all((s['font'],s['size'],s['color'])==(span['font'],span['size'],span['color']) for c,s in hit), 'One match crosses multiple font styles')
                        box=fitz.Rect(hit[0][0]['bbox'])
                        for c,s in hit[1:]:box|=fitz.Rect(c['bbox'])
                        font,data,base=resolve_font(doc,p,span,new,o.get('font_file'))
                        size=span['size']; width=font.text_length(new,fontsize=size)
                        if width>box.width+0.5:
                            need(fit!='error', f'Page {i+1}: replacement is wider ({width:.2f}pt > {box.width:.2f}pt); choose fit=expand or fit=shrink')
                            if fit=='shrink': size*=box.width/width; width=box.width
                        x=box.x1-width if align=='right' else box.x0
                        baseline=hit[0][0]['origin'][1]
                        newbox=fitz.Rect(x,baseline-font.ascender*size,x+width,baseline-font.descender*size)
                        need(p.cropbox.width>=newbox.x1 and newbox.x0>=0 and newbox.y0>=0 and newbox.y1<=p.cropbox.height, 'Replacement extends outside the page')
                        # Reject any expansion into other text, regardless of text block order.
                        for other in p.get_text('words'):
                            wr=fitz.Rect(other[:4])
                            if wr.intersects(newbox) and not wr.intersects(box):
                                raise PDFError(f'Page {i+1}: replacement would overlap adjacent text')
                        for n,(c,s) in enumerate(chars):
                            if not m.start()<=n<m.end() and fitz.Rect(c['bbox']).intersects(newbox):
                                raise PDFError(f'Page {i+1}: replacement would overlap another character')
                        planned.append({'page':i,'box':box,'newbox':newbox,'origin':(x,baseline),'font_data':data,'base':base,
                                        'font':span['font'],'size':size,'original_size':span['size'],'color':span['color']})
        need(planned and len(planned)==expected, f'Found {expected} text matches but can edit {len(planned)}; scanned, fragmented or multi-line text needs a different operation')
        if o.get('expected_count') is not None:need(len(planned)==o['expected_count'], 'expected_count does not match actual occurrences')
        for i in indices:
            p=doc[i]; edits=[e for e in planned if e['page']==i]
            for e in edits:
                # A thin band through the glyph centers avoids deleting tall adjacent lines.
                b=e['box']; band=fitz.Rect(b.x0+0.01,b.y0+b.height*0.45,b.x1-0.01,b.y0+b.height*0.55)
                p.add_redact_annot(band,fill=None,cross_out=False)
            if not edits:continue
            p.apply_redactions(images=0,graphics=0,text=0)
            for n,e in enumerate(edits):
                name=e['base'] or f'PDFToolkitFont{n}'
                if e['font_data']:p.insert_font(fontname=name,fontbuffer=e['font_data'])
                c=e['color'];rgb=((c>>16&255)/255,(c>>8&255)/255,(c&255)/255)
                p.insert_text(e['origin'],new,fontname=name,fontsize=e['size'],color=rgb)
        # Inspect the serialized result, not only the in-memory display list.
        data=doc.tobytes(garbage=4,deflate=True)
        with fitz.open(stream=data,filetype='pdf') as check:
            for i in indices:
                count=sum(e['page']==i for e in planned)
                need(check[i].get_text().count(old)==original_old[i]-count+count*new.count(old), f'Page {i+1}: old text occurrence validation failed')
                need(check[i].get_text().count(new)==original_new[i]+count*(1-old.count(new)), f'Page {i+1}: new text occurrence validation failed')
            new_count=sum(check[i].get_text().count(new) for i in indices)
            # Existing copies of new text are allowed; inserted copies must be extractable.
            need(new_count>=len(planned), 'Replacement text did not survive serialization')
        report=[{'page':e['page']+1,'before_rect':list(e['box']),'after_rect':list(e['newbox']),
                 'font':e['font'],'font_overridden':bool(o.get('font_file')),'font_size':e['size'],
                 'original_font_size':e['original_size'],'color':e['color']} for e in planned]
        out=destination(o,[o['input']])
        result=commit(lambda p:Path(p).write_bytes(data),out,expected_pages=len(doc))
        result.update({'replacements':len(planned),'edits':report,'note':'Text is removed and reinserted. Font/size/color are reused unless explicitly overridden; use compare and inspect rendered pages for layout verification.'})
        return result


def decorate(action,o):
    import pymupdf as fitz
    with open_pdf(o) as doc:
        indices=pages(o.get('pages'),len(doc))
        if action=='redact':
            regions=o.get('regions')
            need(isinstance(regions,list) and regions,'regions must be a non-empty array of {page,rect}')
            for region in regions:
                need(isinstance(region,dict) and type(region.get('page')) is int and region['page']-1 in indices,'Redaction region page must be selected and exist')
                region_rect=rect(region.get('rect'))
                page=doc[region['page']-1]
                need(fitz.Rect(0,0,page.cropbox.width,page.cropbox.height).contains(region_rect),'Redaction rectangle is outside page')
            for i in indices:
                need(not any(a.type[0]==fitz.PDF_ANNOT_REDACT for a in (doc[i].annots() or [])), 'Existing redaction annotations must be handled separately')
        for i in indices:
            p=doc[i]
            if action=='crop':
                p.set_cropbox(rect(o.get('rect')))
            elif action=='redact':
                regions=o.get('regions')
                need(isinstance(regions,list) and regions,'regions must be a non-empty array of {page,rect}')
                for region in regions:
                    if region['page']==i+1:p.add_redact_annot(rect(region['rect']),fill=(0,0,0))
                # Destructive visual redaction, including image pixels and intersecting graphics.
                p.apply_redactions(images=2,graphics=2,text=0)
            else:
                size=number(o.get('font_size',12 if action=='number' else 36),'font_size',1,300)
                opacity=number(o.get('opacity',1 if action=='number' else .2),'opacity',0,1)
                text=(o.get('template','{page} / {total}').replace('{page}',str(i+1)).replace('{total}',str(len(doc)))
                      if action=='number' else o.get('text',''))
                need(isinstance(text,str) and text,'text is required')
                name='helv'
                font=fitz.Font(fontname=name)
                if o.get('font_file'):
                    blob=path(o['font_file']).read_bytes();font=fitz.Font(fontbuffer=blob)
                    name='PDFToolkitDecoration';p.insert_font(fontname=name,fontbuffer=blob)
                need(all(font.has_glyph(ord(c)) for c in text),'Font lacks characters; provide font_file')
                if not o.get('font_file'):need(all(ord(c)<256 for c in text),'Non-Latin text needs font_file')
                width=font.text_length(text,fontsize=size)
                # These operations use unrotated page coordinates.
                x,y=o.get('position',[(p.cropbox.width-width)/2,p.cropbox.height-24 if action=='number' else p.cropbox.height/2])
                number(x,'x');number(y,'y')
                need(x>=0 and x+width<=p.cropbox.width and y-size>=0 and y<=p.cropbox.height,'Decoration does not fit on page')
                color=o.get('color',[0,0,0]);need(isinstance(color,list) and len(color)==3,'color must be RGB [0..1,0..1,0..1]')
                for c in color:number(c,'color',0,1)
                p.insert_text((x,y),text,fontname=name,fontsize=size,color=color,fill_opacity=opacity,overlay=True)
        return save_doc(doc,o)


def create(o):
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from PIL import Image,ImageOps
    action=o['action']; inputs=o.get('inputs',[])
    out=destination(o,inputs)
    if action=='images_to_pdf':
        need(isinstance(inputs,list) and inputs,'inputs must be a non-empty image path array')
        def produce(p):
            from reportlab.lib.utils import ImageReader
            c=canvas.Canvas(p)
            dpi=number(o.get('dpi',144),'dpi',36,600)
            for item in inputs:
                with Image.open(path(item)) as source:
                    im=ImageOps.exif_transpose(source).convert('RGB')
                    width,height=im.width*72/dpi,im.height*72/dpi
                    c.setPageSize((width,height));c.drawImage(ImageReader(im),0,0,width,height);c.showPage()
            c.save()
        return commit(produce,out,expected_pages=len(inputs))
    text=o.get('text');need(isinstance(text,str) and text,'text is required')
    size=number(o.get('font_size',12),'font_size',1,100)
    page_size=o.get('page_size',[595.276,841.89]);need(isinstance(page_size,list) and len(page_size)==2,'page_size requires width,height')
    for n in page_size:number(n,'page dimension',72,14400)
    margin=number(o.get('margin',48),'margin',0,min(page_size)/2-1)
    font='Helvetica'
    if o.get('font_file'):
        font='PDFToolkitFont';pdfmetrics.registerFont(TTFont(font,str(path(o['font_file']))))
        available=pdfmetrics.getFont(font).face.charToGlyph
        need(all(ord(c) in available for c in text if not c.isspace()),'font_file lacks text characters')
    else:need(all(ord(c)<128 for c in text),'Unicode text creation requires font_file')
    def produce(p):
        c=canvas.Canvas(p,pagesize=page_size);y=page_size[1]-margin-size
        for paragraph in text.split('\n'):
            line=''
            chunks=[]
            for ch in paragraph:
                if line and pdfmetrics.stringWidth(line+ch,font,size)>page_size[0]-2*margin:
                    chunks.append(line);line=''
                line+=ch
            chunks.append(line)
            for line in chunks:
                if y<margin+size*.25:c.showPage();y=page_size[1]-margin-size
                c.setFont(font,size);c.drawString(margin,y,line);y-=size*1.4
        c.save()
    return commit(produce,out)


def doctor(_o):
    names=['pypdf','pdfplumber','Pillow','reportlab','PyMuPDF']
    versions={}
    for name in names:
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:versions[name]=None
    return {'python':sys.executable,'versions':versions,'ready':all(versions.values()),
            'optional_tools':{x:shutil.which(x) for x in ('pdftoppm','pdfinfo','tesseract')}}


ACTIONS=('doctor','inspect','extract','render','compare','merge','split','select','rotate','crop',
         'watermark','number','compress','metadata','fill','replace','redact','create_text','images_to_pdf','encrypt','decrypt')


def dispatch(request):
    need(isinstance(request,dict),'Request must be an object')
    action=request.get('action');need(action in ACTIONS,f'Unknown action. Supported: {", ".join(ACTIONS)}')
    o=request.get('options',{});need(isinstance(o,dict),'options must be an object');o={**o,'action':action}
    if action not in ('doctor','inspect','extract','render','compare','encrypt','decrypt','create_text','images_to_pdf'):
        from pypdf import PdfReader
        inputs=o.get('inputs',[]) if action=='merge' else [o.get('input')]
        for source in inputs:
            if source:
                need(not PdfReader(path(source)).is_encrypted,'Decrypt encrypted inputs to a separate copy before editing')
    handlers={'doctor':doctor,'inspect':inspect_pdf,'extract':extract,'render':render,'compare':compare,
              'split':split,'fill':fill,'replace':replace,'create_text':create,'images_to_pdf':create}
    if action in handlers:return handlers[action](o)
    if action in ('merge','select','rotate','compress','metadata','encrypt','decrypt'):return pypdf_transform(action,o)
    return decorate(action,o)


def main():
    try:
        if len(sys.argv)==2 and sys.argv[1]=='--help':
            print('Pass {"action":"...","options":{...}} on stdin. Actions: '+', '.join(ACTIONS));return
        request=json.load(sys.stdin)
        result=dispatch(request)
        print(json.dumps({'ok':True,'result':result},ensure_ascii=False,allow_nan=False))
    except Exception as e:
        print(json.dumps({'ok':False,'error':{'type':type(e).__name__,'message':str(e)}},ensure_ascii=False))
        sys.exit(1)


if __name__=='__main__':main()
