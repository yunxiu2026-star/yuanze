"""Bounded local document extraction. Never runs macros, links or document instructions."""
import io
import json
import re
import sys
import zipfile
from xml.etree import ElementTree as ET

MAX_FILE=10*1024*1024
MAX_TEXT=200_000
EXTENSIONS={'.pdf','.docx','.xlsx','.txt','.md','.csv'}

def archive(data):
    z=zipfile.ZipFile(io.BytesIO(data));items=z.infolist()
    if len(items)>2000 or sum(i.file_size for i in items)>30*1024*1024:
        raise ValueError('文件展开后过大，请拆分资料后上传。')
    if any(i.flag_bits&1 or i.file_size>10*1024*1024 or i.file_size>max(i.compress_size,1)*250 for i in items):
        raise ValueError('压缩文档过大、加密或格式不支持，请另存为普通文档。')
    if any('vbaProject' in i.filename or i.filename.lower().endswith('.bin') for i in items):
        raise ValueError('请上传不含宏或嵌入程序的普通文档。')
    return z

def extract(data,extension):
    if not data or len(data)>MAX_FILE or extension not in EXTENSIONS:raise ValueError('请选择支持的文件，单个文件不超过 10 MB。')
    parts=[];warning='';truncated=False
    if extension in {'.txt','.md','.csv'}:
        encodings=['utf-16'] if data.startswith((b'\xff\xfe',b'\xfe\xff')) else ['utf-8-sig','gb18030']
        for encoding in encodings:
            try:body=data.decode(encoding);break
            except UnicodeError:pass
        else:raise ValueError('无法读取文字编码，请另存为 UTF-8 后上传。')
        if '\x00' in body:raise ValueError('文件不是可读取的纯文本。')
        parts=[body]
    elif extension=='.docx':
        with archive(data) as z:
            raw=z.read('word/document.xml')
            if re.search(br'<!\s*(DOCTYPE|ENTITY)',raw,re.I):raise ValueError('文档 XML 格式不支持。')
            root=ET.fromstring(raw);ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            for p in root.iter(ns+'p'):
                parts.append(''.join(e.text or '' if e.tag==ns+'t' else '\t' if e.tag==ns+'tab' else '\n' if e.tag==ns+'br' else '' for e in p.iter()))
    elif extension=='.xlsx':
        with archive(data) as z:
            for i in z.infolist():
                if i.filename.endswith('.xml') and re.search(br'<!\s*(DOCTYPE|ENTITY)',z.read(i),re.I):raise ValueError('表格 XML 格式不支持。')
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(data),read_only=True,data_only=False,keep_links=False)
        try:
            count=0;size=0
            for sheet in book.worksheets[:20]:
                parts.append('工作表：'+sheet.title)
                for row in sheet.iter_rows():
                    count+=len(row)
                    if count>100_000:truncated=True;break
                    line='\t'.join(str(c.value) if c.value is not None else '' for c in row)
                    parts.append(line);size+=len(line)+1
                    if size>MAX_TEXT:truncated=True;break
                if truncated:break
            if len(book.worksheets)>20:truncated=True
        finally:book.close()
    else:
        if not data.startswith(b'%PDF-'):raise ValueError('PDF 文件格式不正确。')
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(data),strict=False)
        if reader.is_encrypted:
            warning='PDF 已加密，原文件已保留；请在正文中补充可检索资料，或上传未加密版本。'
        else:
            for page in reader.pages[:100]:
                parts.append(page.extract_text() or '')
                if sum(map(len,parts))>MAX_TEXT:truncated=True;break
            if len(reader.pages)>100:truncated=True
    body='\n'.join(parts).replace('\x00','')
    body=re.sub(r'[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]','',body).strip()
    if len(body)>MAX_TEXT:truncated=True;body=body[:MAX_TEXT]
    if truncated:warning='正文提取达到上限（20 万字、PDF 前 100 页或表格前 20 张工作表 / 10 万单元格）；原文件完整保留，请核对并补充摘要。'
    elif not body and not warning:warning='未提取到文字，可能是扫描件或图片内容；原文件已保留，请手动补充正文后提交审核。'
    return dict(body=body,warning=warning,truncated=truncated)

def main():
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(256*1024*1024,256*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    except ImportError:pass
    try:result=extract(sys.stdin.buffer.read(MAX_FILE+1),sys.argv[1]);result['ok']=True
    except ValueError as exc:result=dict(ok=False,error=str(exc)[:200])
    except Exception:result=dict(ok=False,error='文件无法解析，请确认文件未损坏，或另存为普通文档后重试。')
    print(json.dumps(result,ensure_ascii=True))

if __name__=='__main__':main()
