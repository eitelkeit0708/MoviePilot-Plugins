"""Validate existing browser captures and build a GitHub gallery; stdlib only."""
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def jpeg_size(blob):
    assert blob[:2] == b'\xff\xd8', 'Not a JPEG'
    offset = 2
    while offset < len(blob):
        if blob[offset] != 255:
            offset += 1
            continue
        marker = blob[offset + 1]
        offset += 2
        if marker in (216, 217):
            continue
        length = int.from_bytes(blob[offset:offset + 2], 'big')
        if marker in (192, 193, 194):
            return (int.from_bytes(blob[offset + 5:offset + 7], 'big'),
                    int.from_bytes(blob[offset + 3:offset + 5], 'big'))
        offset += length
    raise AssertionError('JPEG has no supported size marker')


data = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
shots = sorted(data['shots'], key=lambda shot: shot['id'])
assert len({shot['id'] for shot in shots}) == len(shots), 'Duplicate screenshot ID'
groups = defaultdict(list)
sequences = defaultdict(list)
dimensions = Counter()
for shot in shots:
    path = ROOT / 'images' / (shot['id'] + '.jpg')
    pixels = path.read_bytes()
    width, height = jpeg_size(pixels)
    assert width >= 1200 and height >= 700, f'Unexpected clipped screenshot: {path.name}'
    dimensions[f'{width}x{height}'] += 1
    shot.update(bytes=len(pixels), sha256=hashlib.sha256(pixels).hexdigest(),
                imageSize={'width': width, 'height': height})
    groups[shot['section']].append(shot)
    if re.search(r'-\d\d$', shot['id']):
        main = next((item for item in shot['scrolls'] if item['tag'] == 'MAIN'), None)
        if main:
            sequences[re.sub(r'-\d\d$', '', shot['id'])].append(main)
for scene, frames in sequences.items():
    assert frames[0]['top'] <= 2, f'{scene}: missing top'
    last = frames[-1]
    assert last['top'] + last['height'] >= last['total'] - 3, f'{scene}: missing bottom'
    for previous, current in zip(frames, frames[1:]):
        assert current['top'] <= previous['top'] + previous['height'] - 75, f'{scene}: gap'
expected = {shot['id'] + '.jpg' for shot in shots}
assert {path.name for path in (ROOT / 'images').glob('*.jpg')} == expected, 'Orphan or missing image'
scenes = {re.sub(r'-\d\d$', '', shot['id']) for shot in shots}
data.update(shots=shots, count=len(shots), sceneCount=len(scenes),
            validation={'jpegFiles': 'PASS', 'minimumWidth': 'PASS',
                        'verticalCoverage': 'PASS', 'segmentedScenesChecked': len(sequences),
                        'imageDimensions': dict(dimensions), 'orphanFiles': 0})
(ROOT / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
lines = [
    '# subscriBetter 前端界面截图 · 2026-10-01', '',
    f'**{len(shots)} 张截图 · {len(scenes)} 个页面或交互状态 · {len(groups)} 个模块图册**', '',
    '覆盖主页面、详情、各设置标签、展开编辑、同等优先合并、共享规则使用、首次配置、迁移、异常提示和浅色主题。长页面从顶部连续拍到底部，按顺序查看即可；相邻分段保留重叠内容。', '',
    '图片来自实际 Vue 前端与本地合成数据预览。黄色顶栏标明合成数据，保存操作仅在预览页模拟。这份图册用于检查界面，不能替代生产服务验收。', '',
    '## 按模块查看', '', '| 图册 | 页面 / 状态数 | 截图数 |', '| --- | ---: | ---: |',
]
for n, (name, items) in enumerate(groups.items(), 1):
    filename = f'{n:02d}-gallery.md'
    scene_groups = defaultdict(list)
    for shot in items:
        scene_groups[re.sub(r'-\d\d$', '', shot['id'])].append(shot)
    lines.append(f'| [{name}]({filename}) | {len(scene_groups)} | {len(items)} |')
    body = [f'# {name}', '', '[返回总目录](README.md)', '',
            '长页面按分段顺序查看；点击图片可打开原图。', '', '## 本图册目录', '']
    for scene, frames in scene_groups.items():
        title = re.sub(r' · \d+$', '', frames[0]['title'])
        body.append(f'- [{title}](#{scene})（{len(frames)} 张）')
    for scene, frames in scene_groups.items():
        title = re.sub(r' · \d+$', '', frames[0]['title'])
        body.extend(['', f'<a id="{scene}"></a>', '', f'## {title}', ''])
        for number, shot in enumerate(frames, 1):
            if len(frames) > 1:
                body.extend([f'### 第 {number} / {len(frames)} 段', ''])
            if shot.get('notes'):
                body.extend([shot['notes'], ''])
            body.extend([f'![{shot["title"]}](images/{shot["id"]}.jpg)', '', f'`{shot["id"]}`', ''])
    (ROOT / filename).write_text('\n'.join(body), encoding='utf-8')
lines.extend(['', '## 重点操作', '',
              '- [同等优先：选择规格](04-gallery.md#21-equal-select) → [合并结果与拆开入口](04-gallery.md#22-equal-merged)',
              '- [全部收录选项](04-gallery.md#30-admission-expanded) · [分类额外条件](04-gallery.md#31-local-conditions)',
              '- [共享规则就地编辑](05-gallery.md#37-rule-inline) → [应用位置](05-gallery.md#39-rule-usage) → [加入策略](05-gallery.md#41-custom-rule-used)',
              '- [首次配置完整流程](08-gallery.md#73-first-plan) · [未保存提示](09-gallery.md#78-unsaved-dialog)', '',
              '## 主要页面预览', ''])
for title, filename, image_id in [
    ('订阅', '01-gallery.md', '02-subscriptions-full-01'),
    ('榜单', '02-gallery.md', '10-boards-01'),
    ('上传与入库', '03-gallery.md', '17-transfers-01'),
    ('质量策略', '04-gallery.md', '20-policy-dv-01'),
    ('下载方案', '06-gallery.md', '42-plan-overview-01'),
    ('设置', '07-gallery.md', '48-settings-runtime-01'),
]:
    lines.extend([f'### [{title}：查看完整图册]({filename})', '', f'![{title}](images/{image_id}.jpg)', ''])
lines.extend(['## 环境与复核', '',
              '桌面浏览器默认视口 1280 × 720。图片保留截图接口返回的原始尺寸（详见清单），未拼接、重绘或修改界面样式。滚动长页面时保留页头、保存栏及焦点样式。', '',
              f'源代码基准：`{data["sourceCommit"]}`。本次只补充版本记录的合成预览响应，生产界面代码未修改。', '',
              '[覆盖范围与限制](COVERAGE.md) · [截图清单、尺寸、哈希与滚动位置](manifest.json)', '',
              '重新校验并生成目录：`python build_gallery.py`。脚本只整理已拍摄的文件，不生成图片。', ''])
(ROOT / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
for markdown in ROOT.glob('*.md'):
    for target in re.findall(r'\]\(([^)]+)\)', markdown.read_text(encoding='utf-8')):
        if target.startswith(('https://', 'http://', '#')):
            continue
        file_part, _, anchor = target.partition('#')
        destination = ROOT / file_part
        assert destination.exists(), f'Broken link in {markdown.name}: {target}'
        if anchor:
            assert f'id="{anchor}"' in destination.read_text(encoding='utf-8'), f'Missing anchor: {target}'
print(f'PASS: {len(shots)} JPEGs, {len(scenes)} scenes, {len(groups)} galleries; '
      f'{len(sequences)} segmented scenes and all local links verified')
