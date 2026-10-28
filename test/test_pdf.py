import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from pdf_toolkit import dispatch, PDFError
import pymupdf as fitz
from pypdf import PdfReader
from PIL import Image
from reportlab.pdfgen import canvas

class PDFToolkitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='pdf toolkit test ')
        self.root=Path(self.tmp.name)
        self.source=self.root/'source.pdf'
        c=canvas.Canvas(str(self.source),pagesize=(400,500))
        for i in range(1,4):
            c.setFont('Helvetica',14);c.drawString(40,440,f'Alpha page {i}')
            c.setFont('Helvetica-Bold',12);c.drawString(40,410,'Keep this unchanged')
            c.showPage()
        c.save()
    def tearDown(self):self.tmp.cleanup()
    def call(self,action,**options):return dispatch({'action':action,'options':options})
    def output(self,name):return str(self.root/name)
    def read(self,p):
        with fitz.open(p) as d:return [x.get_text() for x in d]
    def test_inspect_extract_render(self):
        r=self.call('inspect',input=str(self.source),pages='3,1')
        self.assertEqual([p['page'] for p in r['page_details']],[3,1])
        e=self.call('extract',input=str(self.source),kind='text',output_dir=self.output('text'),pages='2')
        self.assertIn('Alpha page 2',Path(e['files'][0]).read_text())
        p=self.call('render',input=str(self.source),output_dir=self.output('png'),pages='1',dpi=72)
        with Image.open(p['files'][0]) as im:self.assertEqual(im.size,(400,500))
    def test_select_merge_split(self):
        out=self.output('selected.pdf')
        self.call('select',input=str(self.source),output=out,pages='3,1,1')
        text=self.read(out);self.assertIn('page 3',text[0]);self.assertIn('page 1',text[2])
        merge=self.output('merge.pdf')
        self.assertEqual(self.call('merge',inputs=[str(self.source),out],output=merge)['pages'],6)
        r=self.call('split',input=merge,output_dir=self.output('split'),groups=['1-2','6'])
        self.assertEqual([x['pages'] for x in r['outputs']],[2,1])
    def test_rotate_crop_metadata_compress(self):
        r=self.output('rotate.pdf');self.call('rotate',input=str(self.source),output=r,pages='2',angle=90)
        with fitz.open(r) as d:self.assertEqual(d[1].rotation,90);self.assertEqual(d[0].rotation,0)
        c=self.output('crop.pdf');self.call('crop',input=str(self.source),output=c,rect=[20,20,380,480])
        with fitz.open(c) as d:self.assertEqual(d[0].rect.width,360)
        m=self.output('metadata.pdf');self.call('metadata',input=str(self.source),output=m,metadata={'Title':'Example'})
        self.assertEqual(PdfReader(m).metadata.title,'Example')
        z=self.output('compact.pdf');self.call('compress',input=str(self.source),output=z)
        self.assertEqual(self.read(z),self.read(self.source))
        self.assertTrue(self.call('compare',before=str(self.source),after=z)['identical_outside_regions'])
    def test_watermark_number_compare(self):
        w=self.output('watermark.pdf');self.call('watermark',input=str(self.source),output=w,text='DRAFT',pages='1')
        self.assertIn('DRAFT',self.read(w)[0]);self.assertNotIn('DRAFT',self.read(w)[1])
        n=self.output('numbers.pdf');self.call('number',input=str(self.source),output=n,template='Page {page} of {total}')
        self.assertIn('Page 3 of 3',self.read(n)[2])
        self.assertFalse(self.call('compare',before=str(self.source),after=n)['identical_outside_regions'])
        regions=[{'page':i,'rect':[0,460,400,500]} for i in (1,2,3)]
        self.assertTrue(self.call('compare',before=str(self.source),after=n,ignore_regions=regions)['identical_outside_regions'])
    def test_replace_and_preserve_unaffected_pixels(self):
        out=self.output('replace.pdf')
        r=self.call('replace',input=str(self.source),output=out,old='Alpha',new='Beta',expected_count=3)
        self.assertEqual(r['replacements'],3)
        self.assertTrue(all('Alpha' not in t and 'Beta' in t for t in self.read(out)))
        regions=[]
        for e in r['edits']:
            b=fitz.Rect(e['before_rect'])|fitz.Rect(e['after_rect'])
            regions.append({'page':e['page'],'rect':[b.x0-1,b.y0-1,b.x1+1,b.y1+1]})
        diff=self.call('compare',before=str(self.source),after=out,ignore_regions=regions,dpi=144)
        self.assertTrue(diff['identical_outside_regions'],diff)
    def test_replace_rejects_overflow_and_mismatch(self):
        out=self.output('bad.pdf')
        with self.assertRaises(PDFError):self.call('replace',input=str(self.source),output=out,old='Alpha',new='A very long name')
        self.assertFalse(Path(out).exists())
        with self.assertRaises(PDFError):self.call('replace',input=str(self.source),output=out,old='Alpha',new='Beta',expected_count=99)
        self.assertFalse(Path(out).exists())
        with self.assertRaises(PDFError):self.call('replace',input=str(self.source),output=out,old='Alpha',new='A very long name',fit='expand')
        self.assertFalse(Path(out).exists())
    def test_redact(self):
        out=self.output('redacted.pdf')
        self.call('redact',input=str(self.source),output=out,regions=[{'page':1,'rect':[35,35,200,75]}])
        self.assertNotIn('Alpha',self.read(out)[0]);self.assertIn('Alpha',self.read(out)[1])
    def test_create_text_images_extract(self):
        t=self.output('text.pdf');self.call('create_text',output=t,text='Hello\nPDF toolkit')
        self.assertIn('Hello',self.read(t)[0])
        pic=self.root/'image.png';Image.new('RGB',(100,80),'red').save(pic)
        p=self.output('image.pdf');self.call('images_to_pdf',inputs=[str(pic)],output=p,dpi=72)
        with fitz.open(p) as d:self.assertEqual(tuple(d[0].rect),(0,0,100,80))
        r=self.call('extract',input=p,output_dir=self.output('images'),kind='images')
        self.assertEqual(len(r['files']),1)
    def test_tables(self):
        source=self.root/'table.pdf';c=canvas.Canvas(str(source),pagesize=(300,300))
        for x in (20,120,220):c.line(x,200,x,260)
        for y in (200,230,260):c.line(20,y,220,y)
        for x,y,t in [(30,240,'A'),(130,240,'B'),(30,210,'1'),(130,210,'2')]:c.drawString(x,y,t)
        c.save()
        r=self.call('extract',input=str(source),kind='tables',output_dir=self.output('tables'))
        tables=json.loads(Path(r['files'][-1]).read_text())
        self.assertEqual(tables[0]['rows'],[['A','B'],['1','2']])
    def test_form_fill_and_flatten(self):
        source=self.root/'form.pdf';c=canvas.Canvas(str(source),pagesize=(400,500))
        c.acroForm.textfield(name='name',x=30,y=400,width=200,height=30,value='Before')
        c.acroForm.checkbox(name='agree',x=30,y=350,checked=False)
        c.showPage();c.save()
        out=self.output('filled.pdf')
        self.call('fill',input=str(source),output=out,values={'name':'Alice','agree':'/Yes'})
        r=PdfReader(out);self.assertEqual(r.get_fields()['name']['/V'],'Alice')
        flat=self.output('flat.pdf')
        self.call('fill',input=out,output=flat,values={'name':'Bob'},flatten=True)
        self.assertFalse(PdfReader(flat).get_fields());self.assertIn('Bob',self.read(flat)[0])
    def test_encryption(self):
        encrypted=self.output('encrypted.pdf')
        self.call('encrypt',input=str(self.source),output=encrypted,user_password='test-only')
        self.assertTrue(PdfReader(encrypted).is_encrypted)
        with self.assertRaises(PDFError):self.call('inspect',input=encrypted,password='wrong')
        clear=self.output('decrypted.pdf');self.call('decrypt',input=encrypted,output=clear,password='test-only')
        self.assertEqual(self.read(clear),self.read(self.source))
        self.assertFalse(PdfReader(clear).is_encrypted)
    def test_no_overwrite_invalid_pages(self):
        original=self.source.read_bytes()
        with self.assertRaises(PDFError):self.call('rotate',input=str(self.source),output=str(self.source))
        self.assertEqual(original,self.source.read_bytes())
        with self.assertRaises(PDFError):self.call('select',input=str(self.source),output=self.output('x.pdf'),pages='0')
        with self.assertRaises(PDFError):self.call('inspect',input=str(self.source),pages='100')
    def test_encrypted_edit_requires_explicit_decrypt(self):
        source=self.output('encrypted.pdf')
        self.call('encrypt',input=str(self.source),output=source,user_password='pw')
        with self.assertRaises(PDFError):self.call('rotate',input=source,password='pw',output=self.output('edit.pdf'))
    def test_invalid_redaction_is_not_silently_ignored(self):
        out=self.output('invalid-redaction.pdf')
        with self.assertRaises(PDFError):self.call('redact',input=str(self.source),output=out,regions=[{'page':99,'rect':[0,0,10,10]}])
        self.assertFalse(Path(out).exists())
    def test_replacement_may_contain_old_text(self):
        out=self.output('prefix.pdf')
        self.call('replace',input=str(self.source),output=out,old='Alpha',new='AlphaX',fit='shrink')
        self.assertTrue(all('AlphaX' in t for t in self.read(out)))
    def test_doctor(self):self.assertTrue(self.call('doctor')['ready'])

if __name__=='__main__':unittest.main(verbosity=2)
