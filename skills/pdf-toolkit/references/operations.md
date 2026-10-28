# 操作参数

所有示例中的 `/path/...` 仅表示用户选择的绝对路径。后端也可直接接受相同请求：

```sh
python /path/dsh-pdf-toolkit/python/pdf_toolkit.py < request.json
```

请求格式：`{"action":"rotate","options":{"input":"/path/source.pdf","output":"/path/rotated.pdf","angle":90}}`。
成功输出 `{"ok":true,"result":...}`；失败输出 `{"ok":false,"error":{"type":"...","message":"..."}}` 并退出 1。

Harness 中调用 `pdf_process({action,options})`。`pdf_inspect/pdf_extract/pdf_render/pdf_compare` 直接接受 options 的字段；`pdf_doctor({})` 无参数。

## 共用规则

- 所有输出使用新路径。单文件 PDF 操作先写临时文件、重开校验、渲染，再发布结果。目标已存在或与输入相同则失败。
- `pages` 为从 1 开始的字符串：`"all"`、`"1,3-5"`、`"5-3,1"`。select 和 split 的组允许重复页，其余操作不允许重复。
- 多文件提取/拆分/渲染写入新目录，过程中出错可能留下部分文件，不能把该目录视为已完成交付。
- 编辑坐标单位为 PDF point（72 pt = 1 inch），左上原点，使用**未旋转页面**坐标。crop 使用原始页面 CropBox 坐标。compare 的忽略区域使用**渲染后页面**坐标，旋转页需要转换。
- `password` 用于读取加密输入。请勿把密码写进报告或可共享日志。

| action | 必填 options | 可选参数与行为 |
|---|---|---|
| doctor | 无 | Python 和依赖版本、可选 pdftoppm/pdfinfo/tesseract 检测 |
| inspect | input | pages, password；返回所有选中页的字体、尺寸、图像数、字段 |
| extract | input, output_dir | kind=text/tables/images（默认 text）, pages, password；后端还支持 table_settings（pdfplumber 表格配置） |
| render | input, output_dir | pages, dpi=144（36–600）, password；完整页面 PNG |
| compare | before, after | pages, dpi=120（36–300）, output_dir, ignore_regions；输入页数和尺寸必须相同 |
| merge | inputs[], output | passwords：以输入路径为键的密码映射（应先显式解密，见技能）；保持输入顺序 |
| split | input, output_dir | groups=["1-3","4-6"]；默认一页一文件 |
| select | input, output, pages | 选页、删除不需要的页、重排和复制；如 "3,1,1" |
| rotate | input, output | angle=90（90 的整数倍）, pages；顺时针相对旋转 |
| crop | input, output, rect | pages；rect=[x0,y0,x1,y1]，不删除框外内容 |
| watermark | input, output, text | pages, font_size=36, opacity=0.2, color=[0,0,0], position=[x,y], font_file；position 是文本基线起点，默认居中 |
| number | input, output | pages, template="{page} / {total}", font_size=12, opacity=1, color, position, font_file；页码用原文档页序号 |
| compress | input, output | 压缩内容流、去重对象，不重采样图片；结果不保证更小，返回 saved_bytes |
| metadata | input, output, metadata | metadata={"Title":"...","Author":"..."}；合并指定字段，不删除其他元数据 |
| fill | input, output, values | values={"field":"text","checkbox":"/Yes"}, flatten=false；checkbox/radio 必须使用实际状态名；不支持 XFA 或填写签名字段 |
| replace | input, output, old, new | pages, expected_count, fit=error/expand/shrink, align=left/right, font_file；字面精确匹配，重用原字体/字号/颜色；缺字报错；不修改后续文字位置 |
| redact | input, output, regions | regions=[{"page":1,"rect":[10,20,100,50]}], pages；黑色覆盖并删除该区域文字/图像像素/相交矢量图形 |
| create_text | output, text | page_size=[595.276,841.89]（A4）, font_size=12, margin=48, font_file；基础换行和分页，不是 HTML/Office 排版引擎 |
| images_to_pdf | inputs[], output | dpi=144；按输入次序每图一页，纠正 EXIF 方向，保留图片比例 |
| encrypt | input, output, user_password | owner_password, password（原密码）；AES-256 |
| decrypt | input, output, password | 另存未加密副本 |

## 举例

合并：

```json
{"action":"merge","options":{"inputs":["/path/a.pdf","/path/b.pdf"],"output":"/path/combined.pdf"}}
```

重排并删除其余页面：

```json
{"action":"select","options":{"input":"/path/source.pdf","pages":"5,1-3","output":"/path/reordered.pdf"}}
```

填写表单：

```json
{"action":"fill","options":{"input":"/path/form.pdf","values":{"customer":"Example Name"},"output":"/path/filled.pdf"}}
```

文字替换：

```json
{"action":"replace","options":{"input":"/path/source.pdf","old":"Draft","new":"Final","expected_count":2,"output":"/path/final.pdf"}}
```

新文字太宽时默认报错，不会自动改变字号。只有用户允许相应变化时选择 expand（扩大文字占用区域，拒绝与邻字冲突）或 shrink（缩小字号）。不进行自动整段重排。

差异检查：

```json
{"action":"compare","options":{"before":"/path/source.pdf","after":"/path/final.pdf","ignore_regions":[{"page":1,"rect":[40,50,140,80]}],"output_dir":"/path/differences"}}
```

`replace` 返回每次修改的原/新区域、字号和字体来源，可据此确定差异掩码；需要增加少量抗锯齿边界。必须查看修改区域内的内容，掩码不是免检区域。
