"""Asset representation guidance and deterministic scene-structure checks."""
import json
import re


PROXY_KINDS = {'box', 'sphere', 'cylinder', 'container', 'bin', 'composite'}

_STRUCTURE_ID = re.compile(
    r'^(?:floor|ground|wall|ceiling|roof|foundation|terrain|pavement|asphalt|road|'
    r'beam|column|pillar|stair|staircase|ramp|retaining_wall|door_frame|window_frame|'
    r'door_opening|window_opening|facade|building_shell|bay_line)(?:_|$)', re.I)
_STRUCTURE_TEXT = re.compile(
    r'^\s*(?:(?:the|a|an|fixed|structural|architectural|concrete|brick|stone|'
    r'reinforced|interior|exterior)\s+)?'
    r'(?:building\s+(?:shell|facade|structure|exterior)|'
    r'floor\s+(?:slab|plane|surface|tile)|ground\s+(?:plane|surface)|walls?|'
    r'ceiling|roof|foundation|terrain|pavement|asphalt|road|beam|column|pillar|'
    r'stairs?|staircase|ramp|retaining wall|door\s+(?:frame|opening)|'
    r'window\s+(?:frame|opening)|facade|curb)\b|'
    r'^\s*(?:地板|地面|墙体|墙壁|隔墙|天花板|天花|顶棚|屋顶|屋面|地基|基础|承重梁|横梁|'
    r'立柱|承重柱|承重墙|楼梯|台阶|坡道|道路|路面|地形|挡土墙|门洞|窗洞|门框|窗框|建筑立面|建筑外壳|路缘)', re.I)
_NONSTRUCTURAL_MOUNTED_OBJECT = re.compile(
    r'wall[- ]?(?:lamp|light|art|clock|shelf|cabinet|mounted tv)|ceiling[- ]?(?:lamp|light|fan)|'
    r'floor[- ]?(?:lamp|mat|rug)|road[- ]?sign|墙灯|墙面灯|墙上装饰|吊灯|落地灯|'
    r'地毯|地垫|路牌|交通标志', re.I)


GUIDANCE = '''
Asset representation policy:
- Reuse suitable existing assets. FastTrellis is recommended for reconstructing recognizable standalone objects from images. Authored geometry and registered or imported assets can also meet the brief. Verify the required identity, appearance and function from the actual capture.
- For image-led scenes, benchforge_layout can return initial positions, dimensions, masks and registered mesh IDs. Adjust the estimates against the actual supports, geometry and clearances when authoring the SceneProgram.
- Author exact geometry where the scene requires precise dimensions or openings. For moving robots, select a compatible Isaac Sim articulated asset from the live catalog or upload a supported model. Verify its joints, drives and requested behavior from native evidence.
- Only require specialist geometry or materials when the task explicitly depends on optical behavior, flexible deformation, or another property the generated mesh cannot provide. A visual approximation of glass, a cable, or a flexible part may still use FastTrellis when those properties are not evaluated.
- Use articulation_required, kinematics_required, optical_accuracy_required, or flexible_deformation_required only for the corresponding explicit task requirement.
- Use declared mass and supported colliders for dynamic rigid bodies. Verify actual contact and support independently of how an asset was created.
'''.strip()


def is_structural_geometry(obj):
    """Recognize large static building geometry without classifying mounted props."""
    size = obj.get('size', [])
    if (obj.get('dynamic') is not False or len(size) != 3 or
            min(size) > 0.6 or max(size) < 1.0):
        return False
    tags = {str(tag).lower() for tag in obj.get('tags', [])}
    if tags.intersection({'architectural_structure', 'building_shell', 'native_structure'}):
        return True
    text = ' '.join((str(obj.get('id', '')), str(obj.get('label', '')), *map(str, obj.get('tags', []))))
    if _NONSTRUCTURAL_MOUNTED_OBJECT.search(text):
        return False
    if _STRUCTURE_ID.match(str(obj.get('id', ''))):
        return True
    labels = (obj.get('label', ''), *obj.get('tags', []))
    return any(_STRUCTURE_TEXT.match(re.sub(r'[_-]+', ' ', str(value)).strip())
               for value in labels)


def native_asset_requirement(obj):
    """Return the declared specialist need that makes image reconstruction unsuitable."""
    tags = {str(tag).lower() for tag in obj.get('tags', [])}
    requirements = {
        'articulation_required': 'jointed or articulated function',
        'kinematics_required': 'measurable kinematics or controlled motion',
        'optical_accuracy_required': 'verified optical material behavior',
        'flexible_deformation_required': 'flexible or deformable geometry',
    }
    return next((reason for tag, reason in requirements.items() if tag in tags), None)


def layout_native_reason(label, *, requires_articulation=False):
    """Return why an image-layout object should bypass TRELLIS, if necessary."""
    if requires_articulation:
        return 'the requested function needs an articulated or controlled body'
    text = str(label or '')
    if _NONSTRUCTURAL_MOUNTED_OBJECT.search(text):
        return None
    normalized = re.sub(r'[_-]+', ' ', text).strip()
    normalized = re.sub(r'^(?:the|a|an)\s+', '', normalized, flags=re.I)
    if normalized.casefold() in {'floor', 'ground', '地板', '地面'} or _STRUCTURE_TEXT.match(normalized):
        return 'fixed architectural geometry needs authored dimensions and openings'
    return None


# 按有限原生例外分类；普通可见对象始终优先交给 FastTrellis。
def layout_representation(item):
    """Choose native only for authored structure or explicitly articulated function."""
    reason = layout_native_reason(
        item.get('label'), requires_articulation=item.get('requires_articulation', False))
    if reason:
        return 'native', reason
    if item.get('representation') == 'native':
        return 'trellis', 'ordinary visible objects default to FastTrellis'
    return 'trellis', None


def registered_mesh_status(root, asset_id):
    """Read the registered identity from the same access scope as its geometry."""
    from capabilities.asset_generation.asset_access import registered_asset_path
    from service_access.access_scope import current_access

    try:
        path = registered_asset_path(root, asset_id, access=current_access())
        asset = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if isinstance(asset, dict) and asset.get('asset_id') == asset_id:
        return asset.get('status')
    return None
