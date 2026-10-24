"""Bounded physical-cell identity OCR; no provider calls or sample values."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from app.services.schedule_a_final_evidence import completed_document_source_pages
from app.services.schedule_a_hartford_columns import extract_hartford_columnar_brokers


def recover_hartford_scanned_identities(file_bytes,file_name,page_texts,raw,provider_rows):
    """Scope expensive cell OCR to an incomplete scanned Hartford statement."""
    if (not isinstance(raw,dict) or not isinstance(raw.get('local_ocr_pages'),list)
        or not raw['local_ocr_pages'] or not str(file_name).lower().endswith('.pdf')
        or not file_bytes.startswith(b'%PDF')):
        return []
    packet='\n'.join(text for _,text in page_texts).upper()
    if 'HARTFORD' not in packet or 'ANNUAL STATEMENT OF PREMIUMS AND PRODUCER COMPENSATION' not in packet:
        return []
    bound=completed_document_source_pages(raw,file_name)
    pages=[page for page,text in bound if re.search(r'Producer\s+and\s+Address',text,re.I)
        and re.search(r'\bCommissions\b',text,re.I)]
    current=extract_hartford_columnar_brokers(page_texts)
    if provider_rows and len(current)>=len(provider_rows) and all(row.decision=='AUTOMATIC' for row in current):
        return []
    return read_hartford_grid_identities(file_bytes,pages)


def _pgm(contents):
    tokens=[]
    position=0
    while len(tokens)<4:
        while contents[position] in b' \t\r\n':
            position+=1
        if contents[position]==35:
            position=contents.index(b'\n',position)+1
            continue
        end=position
        while contents[end] not in b' \t\r\n':
            end+=1
        tokens.append(contents[position:end])
        position=end
    position+=2 if contents[position:position+2]==b'\r\n' else 1
    width,height=map(int,tokens[1:3])
    if tokens[0]!=b'P5' or tokens[3]!=b'255' or not 0<width*height<=32_000_000:
        raise ValueError('Unsupported bounded grayscale image')
    pixels=contents[position:]
    if len(pixels)!=width*height:
        raise ValueError('Incomplete rendered image')
    return width,height,pixels


def _centers(values):
    groups=[]
    for value in values:
        if groups and value==groups[-1][-1]+1:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [round(sum(group)/len(group)) for group in groups]


def _identity(text,code):
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if len(lines)<3 or not re.fullmatch(r'\d{1,2}',code.strip()):
        return None
    city=re.fullmatch(r"(.+?),?\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)",lines[-1])
    address=next((index for index,line in enumerate(lines[:-1])
        if re.match(r'^(?:\d|P\.?\s*O\.?\s*BOX\b)',line,re.I)),None)
    if not city or address is None or address==0:
        return None
    return {'name':' '.join(lines[:address]),'address_line_1':' '.join(lines[address:-1]),
        'city':city.group(1).rstrip(','),'state':city.group(2),'zip_code':city.group(3),
        'organization_code':code.strip()}


def read_hartford_grid_identities(file_bytes,page_numbers):
    """Read identity/code cells beneath matching headers on seven-column grids.

    Called only by the Hartford scanned-source recovery path. Coordinates come
    from the actual page grid. Currency cells are not read or changed here.
    A missing runtime, incomplete grid/header or unreadable cell yields no
    identity for that source location; it never supplies a default value.
    Two different segmentation modes must agree on all identity/code fields.
    """
    renderer,ocr=shutil.which('pdftoppm'),shutil.which('tesseract')
    pages=sorted(set(page_numbers))
    if not renderer or not ocr or not file_bytes or not pages or len(pages)>4:
        return []
    if any(type(page) is not int or not 1<=page<=20 for page in pages):
        return []
    started=time.monotonic()
    records=[]
    try:
        with tempfile.TemporaryDirectory(prefix='hartford-source-grid-') as temporary:
            temporary=Path(temporary)
            source=temporary/'source.pdf'
            source.write_bytes(file_bytes)
            for page in pages:
                if time.monotonic()-started>120:
                    break
                prefix=temporary/f'page-{page}'
                subprocess.run([renderer,'-f',str(page),'-l',str(page),'-singlefile','-gray','-r','400',
                    str(source),str(prefix)],check=True,capture_output=True,timeout=60)
                image=prefix.with_suffix('.pgm')
                if image.stat().st_size>33_000_000:
                    continue
                width,height,pixels=_pgm(image.read_bytes())
                binary=pixels.translate(bytes(0 if value<160 else 255 for value in range(256)))
                long_line=re.compile(b'\x00{'+str(int(width*.65)).encode()+b',}')
                horizontal=_centers([y for y in range(height) if long_line.search(binary[y*width:(y+1)*width])])
                previous=None
                for index,(y1,y2) in enumerate(zip(horizontal,horizontal[1:])):
                    if time.monotonic()-started>120:
                        break
                    top,bottom=y1+5,y2-5
                    if bottom-top<20:
                        previous=None
                        continue
                    vertical=_centers([x for x in range(width)
                        if binary[top*width+x:bottom*width+x:width].count(0)>=.94*(bottom-top)])
                    if len(vertical)!=8:
                        previous=None
                        continue
                    cells=[]
                    for column in [0,1]:
                        left,right=vertical[column]+4,vertical[column+1]-4
                        if right<=left:
                            raise ValueError('Invalid cell bounds')
                        crop=b''.join(pixels[y*width+left:y*width+right] for y in range(top,bottom))
                        cell=temporary/f'cell-{page}-{index}-{column}.pgm'
                        cell.write_bytes(f'P5\n{right-left} {bottom-top}\n255\n'.encode()+crop)
                        result=subprocess.run([ocr,str(cell),'stdout','--psm','6'],check=True,
                            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
                        cells.append({'bounds':[left,top,right,bottom],'text':result.stdout.strip()})
                    labels=[re.sub(r'[^a-z]','',cell['text'].casefold()) for cell in cells]
                    if labels==['producerandaddress','orgcode']:
                        previous=(y2,vertical)
                        continue
                    identity=_identity(cells[0]['text'],cells[1]['text'])
                    if identity and previous and previous[0]==y1 and previous[1]==vertical:
                        secondary=[]
                        for column in [0,1]:
                            cell=temporary/f'cell-{page}-{index}-{column}.pgm'
                            if time.monotonic()-started>120:
                                return []
                            mode='7' if column==1 else '4'
                            response=subprocess.run([ocr,str(cell),'stdout','--psm',mode],check=True,
                                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
                            secondary.append(response.stdout.strip())
                        confirmed=_identity(*secondary)
                        normalize=lambda value:re.sub(r'[^a-z0-9]','',str(value).casefold())
                        if not confirmed or any(normalize(identity[key])!=normalize(confirmed[key]) for key in identity):
                            previous=None
                            continue
                        records.append({**identity,'page':page,'identity_bounds':cells[0]['bounds'],
                            'code_bounds':cells[1]['bounds'],'image_dimensions':[width,height],
                            'source_text':'Producer and Address\n'+cells[0]['text']+'\nOrg Code\n'+cells[1]['text'],
                            'identity_read_agreement':True,'independent_identity_read':{
                                'identity_psm':4,'code_psm':7,'identity':confirmed,
                                'source_text':'Producer and Address\n'+secondary[0]+'\nOrg Code\n'+secondary[1]}})
                    previous=None
    except (OSError,ValueError,IndexError,subprocess.SubprocessError):
        return []
    return records
