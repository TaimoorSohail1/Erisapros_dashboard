from io import BytesIO
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject

def source_pdf(*,fee='20.00',schedule_c=False):
 writer=PdfWriter();page=writer.add_blank_page(width=650,height=600)
 font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
 items=[(36,570,'This information is used on your Schedule A (Form 5500)'),(36,550,'Contract or ID Number: TEST-1'),
 (36,515,'Service Provider Information'),(36,490,'Total Amount of Commissions Paid: $100.00'),(36,470,'Total Fees Paid/Amount: $'+fee),
 (36,420,'Name and Address of Recipient of Fees or Commissions'),(36,390,'Example Brokerage LLC'),(350,390,'Organization Code 3'),
 (36,370,'123 MAIN ST'),(350,370,'Amount of Commissions Paid: $100.00'),(36,350,'BOSTON, MA 02110'),(350,350,'Fees Paid/Amount $20.00')]
 if schedule_c:items.append((36,300,'Schedule C Service Provider Information'))
 text=''
 for x,y,v in items:
  safe=v.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
  text+=f'BT /F1 10 Tf {x} {y} Td ({safe}) Tj ET\n'
 stream=DecodedStreamObject();stream.set_data(text.encode('ascii'));page[NameObject('/Contents')]=writer._add_object(stream)
 out=BytesIO();writer.write(out);return out.getvalue()

def ruled_source_pdf(*, total='100.00'):
 writer=PdfWriter();page=writer.add_blank_page(width=860,height=600)
 font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
 page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
 xs=[30,140,440,540,680,820];ys=[530,480,410,380,350]
 text=''.join(f'{x} {ys[-1]} m {x} {ys[0]} l S\n' for x in xs)
 text+=''.join(f'{xs[0]} {y} m {xs[-1]} {y} l S\n' for y in ys)
 cells=[['Benefit','Name and Address of the Agents','Agent Number','Total Commissions Paid','Total Amount of Fees Paid'],
 ['LIFE','Example Brokerage LLC\n123 MAIN ST\nBOSTON, MA 02110','12345','$100.00','$20.00'],
 ['Benefit total','','','$'+total,'$20.00'],['Grand Total','','','$'+total,'$20.00']]
 for row,c in enumerate(cells):
  for col,value in enumerate(c):
   for line,s in enumerate(value.splitlines()):
    s=s.replace('(','\\(').replace(')','\\)')
    text+=f'BT /F1 8 Tf {xs[col]+4} {ys[row]-14-line*12} Td ({s}) Tj ET\n'
 stream=DecodedStreamObject();stream.set_data(text.encode('ascii'));page[NameObject('/Contents')]=writer._add_object(stream)
 out=BytesIO();writer.write(out);return out.getvalue()
