"""Codex ContentItem -> legacy UI events, with loss-detecting verification.

The official engine accepts input_text/output_text, input_image (URL or file ID),
and input_audio. Preserve model content order separately from legacy UI grouping.
No attachment downloads, file rewrites, or text-only substitution on failures.
"""
import server as upstream

TEXT = {'input_text', 'output_text', 'text'}
DETAIL = {None, 'auto', 'low', 'high', 'original'}


def content_parts(content, role, location):
    if not isinstance(content, list):
        raise upstream.OperationError(f'{location} 消息 content 不是数组')
    out = []
    for block in content:
        if not isinstance(block, dict):
            raise upstream.OperationError(f'{location} 消息内容块结构异常')
        kind = block.get('type')
        if kind in TEXT:
            text = block.get('text')
            if not isinstance(text, str):
                raise upstream.OperationError(f'{location} 来源文本字段无效')
            if role == 'user' and (upstream.is_internal_user_message(text) or text.lstrip().startswith('# AGENTS.md instructions')):
                continue
            out.append({'type': ('input_text' if role == 'user' else 'output_text') if kind == 'text' else kind, 'text': text})
        elif kind == 'input_image':
            keys = [key for key in ('image_url', 'file_id') if key in block]
            if len(keys) != 1 or not isinstance(block[keys[0]], str) or not block[keys[0]].strip():
                raise upstream.OperationError(f'{location} 图片引用无效：须有唯一的 image_url 或 file_id')
            if block.get('detail') not in DETAIL:
                raise upstream.OperationError(f'{location} 图片 detail 字段无效')
            out.append({'type': kind, keys[0]: block[keys[0]], **({'detail': block['detail']} if block.get('detail') is not None else {})})
        elif kind == 'input_audio':
            if not isinstance(block.get('audio_url'), str) or not block['audio_url'].strip():
                raise upstream.OperationError(f'{location} 音频 audio_url 字段无效')
            out.append({'type': kind, 'audio_url': block['audio_url']})
        else:
            raise upstream.OperationError(f'{location} 含尚未识别的内容类型 {str(kind)[:80]}；未丢弃该内容，请检查记录格式')
    if role == 'assistant' and any(c['type'] not in TEXT for c in out):
        raise upstream.OperationError(f'{location} 助手消息的附件尚无已验证展示格式；原件保留，未按纯文本处理')
    return out


def media_values(content):
    # Legacy Codex history groups images then audio. Original interleaving is
    # retained and independently verified in response_item.content.
    images = []; audio = []
    for block in content:
        if block['type'] == 'input_image':
            key = 'image_url' if 'image_url' in block else 'file_id'
            images.append(['image', key, block[key], block.get('detail')])
        elif block['type'] == 'input_audio':
            audio.append(['audio', block['audio_url']])
    return images + audio


def visible_value(role, text, media):
    # Keep the established text-only digest format for compatibility.
    return [role, text, media] if media else [role, text]


def raw_value(message):
    return [message['role'], message['content'], message['phase']]


def history_event(message):
    if message['role'] == 'assistant':
        return {'type': 'agent_message', 'message': message['text'], 'phase': message['phase']}
    event = {'type': 'user_message', 'message': message['text'], 'images': [], 'image_details': [],
             'file_ids': [], 'file_id_details': [], 'image_order': [], 'local_images': [],
             'audio': [], 'local_audio': [], 'text_elements': []}
    for block in message['content']:
        if block['type'] == 'input_image':
            inline = 'image_url' in block
            event['images' if inline else 'file_ids'].append(block['image_url' if inline else 'file_id'])
            event['image_details' if inline else 'file_id_details'].append(block.get('detail'))
            event['image_order'].append('inline' if inline else 'file')
        elif block['type'] == 'input_audio':
            event['audio'].append(block['audio_url'])
    return event


def official_value(item):
    if item['type'] == 'agentMessage':
        return visible_value('assistant', item['text'], [])
    if item['type'] != 'userMessage':
        return None
    text = []; media = []
    for block in item['content']:
        kind = block['type']
        if kind == 'text':
            text.append(block['text'])
        elif kind == 'image':
            key = 'url' if 'url' in block else 'fileId'
            media.append(['image', 'image_url' if key == 'url' else 'file_id', block[key], block.get('detail')])
        elif kind == 'audio':
            media.append(['audio', block['url']])
        else:
            raise upstream.OperationError(f'官方合并历史出现未预期内容类型 {kind}，未报告核验成功')
    return visible_value('user', '\n\n'.join(text), media)
