"""Native assets cannot hide service filesystem references in their contents."""

import importlib.util
import json
import os
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from capabilities.isaac_sim.upload_access import snapshot_upload, validate_upload
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context


class IsaacUploadAccessTests(unittest.TestCase):
    # 创建独立临时上传目录，隔离各种容器格式的依赖引用检查。
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    # 以 UTF-8 写入指定名称的测试资源，返回待验证路径。
    def file(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    # 验证 URDF 网格及纹理引用必须是安全的资源内部相对路径。
    def test_urdf_mesh_and_texture_references_are_checked(self):
        for tag in ('mesh', 'texture'):
            for reference in ('/etc/private', '../other-asset/file', 'file:///tmp/x', 'package://other/mesh'):
                with self.subTest(tag=tag, reference=reference):
                    path = self.file('robot.urdf', f'<robot name="r"><{tag} filename="{reference}"/></robot>')
                    with self.assertRaises(PermissionError):
                        validate_upload(path)
        validate_upload(self.file('valid.urdf', '<robot name="r"><mesh filename="meshes/part.stl"/><texture filename="textures/a.png"/></robot>'))

    # 验证 MJCF 包含文件、网格与纹理目录均执行相同引用边界检查。
    def test_mjcf_includes_and_compiler_directories_are_checked(self):
        for fragment in ('<include file="../other.xml"/>', '<compiler meshdir="/etc"/>',
                         '<compiler texturedir="file:///etc"/>', '<texture file="../secret.png"/>'):
            with self.subTest(fragment=fragment), self.assertRaises(PermissionError):
                validate_upload(self.file('robot.xml', '<mujoco>' + fragment + '</mujoco>'))
        validate_upload(self.file('valid.xml', '<mujoco><compiler meshdir="mesh"/><include file="parts/arm.xml"/></mujoco>'))

    # 验证上传 XML 拒绝文档类型与实体定义，避免实体解析绕过检查。
    def test_xml_entities_are_not_accepted(self):
        with self.assertRaises(ValueError):
            validate_upload(self.file('r.urdf', '<!DOCTYPE robot [<!ENTITY value "x">]><robot name="&value;"/>'))

    # 验证文本 glTF 与 GLB 内每个 URI 都经检查，同时允许合法内联数据。
    def test_gltf_and_glb_check_every_uri(self):
        for reference in ('/etc/private', '..%2Fsecret', '../x', 'https://example.com/x'):
            value = {'asset': {'version': '2.0'}, 'images': [{'uri': reference}]}
            with self.subTest(reference=reference):
                with self.assertRaises(PermissionError):
                    validate_upload(self.file('mesh.gltf', json.dumps(value)))
                raw = json.dumps(value).encode()
                raw += b' ' * (-len(raw) % 4)
                path = self.root / 'mesh.glb'
                path.write_bytes(struct.pack('<4sII', b'glTF', 2, 20 + len(raw)) +
                                 struct.pack('<II', len(raw), 0x4E4F534A) + raw)
                with self.assertRaises(PermissionError):
                    validate_upload(path)
        validate_upload(self.file('valid.gltf', '{"buffers":[{"uri":"data:application/octet-stream;base64,YQ=="}]}'))

    # 验证 OBJ 和 MTL 的材质、纹理及外部调用命令不能引用资源外位置。
    def test_wavefront_file_references_and_commands_are_checked(self):
        for suffix, contents in (('.obj', 'mtllib ../private.mtl'), ('.obj', 'call /etc/private'),
                                 ('.mtl', 'map_Kd /etc/private.png'), ('.mtl', 'bump -s 1 1 1 ../private.png')):
            with self.subTest(suffix=suffix, contents=contents), self.assertRaises(PermissionError):
                validate_upload(self.file('mesh' + suffix, contents))
        validate_upload(self.file('good.obj', 'mtllib materials/material.mtl\nv 0 0 0'))
        validate_upload(self.file('good.mtl', 'map_Kd -s 1 1 1 textures/color.png'))

    # 验证解析器按最终远端文件名选择格式，并拒绝越界名称及未知格式。
    def test_remote_filename_controls_parser_and_cannot_traverse(self):
        path = self.file('pretend.bin', '<robot><texture filename="/etc/private"/></robot>')
        with self.assertRaises(PermissionError):
            validate_upload(path, 'robot.urdf')
        for name in ('../foreign/source.bin', '/tmp/source.bin', 'C:source.bin'):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                validate_upload(path, name)
        with self.assertRaises(ValueError):
            validate_upload(path, 'unknown.fbx')

    # 验证 USD 子层、引用、载荷和时间采样中的资源路径均被检查。
    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_usd_assets_sublayers_payloads_and_samples_are_checked(self):
        documents = [
            '#usda 1.0\n( subLayers = [@../private.usda@] )\n',
            '#usda 1.0\ndef Xform "W" ( prepend references = @/etc/private.usda@ )\n{\n}\n',
            '#usda 1.0\ndef Xform "W" ( prepend payload = @../private.usda@ )\n{\n}\n',
            '#usda 1.0\ndef Xform "W"\n{\n asset texture = @../private.png@\n}\n',
            '#usda 1.0\ndef Xform "W"\n{\n asset texture.timeSamples = {0: @../private.png@}\n}\n',
        ]
        for text in documents:
            with self.subTest(text=text), self.assertRaises(PermissionError):
                validate_upload(self.file('scene.usda', text))
        validate_upload(self.file('good.usda', '#usda 1.0\ndef Xform "W"\n{\n asset texture = @textures/own.png@\n}\n'))

    # 验证合法材质连接不被误拒绝，同时仍能发现着色器中的外部资源引用。
    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_usd_material_connections_preserve_asset_validation(self):
        document = '''#usda 1.0
def Xform "World" {
    def Mesh "Mesh" {
        rel material:binding = </World/Material>
    }
    def Material "Material" {
        token outputs:surface.connect = </World/Material/Shader.outputs:surface>
        def Shader "Shader" {
            asset inputs:file = @TEXTURE@
            token outputs:surface
        }
    }
}
'''
        validate_upload(self.file('material.usda', document.replace('TEXTURE', 'textures/own.png')))
        with self.assertRaises(PermissionError):
            validate_upload(self.file('foreign.usda', document.replace('TEXTURE', '../private.png')))

    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_scalar_display_color_warns_and_constant_array_passes(self):
        scalar = self.file('color.usda', '''#usda 1.0
def Cube "Wall" {
    uniform color3f primvars:displayColor = (0.72, 0.68, 0.62)
}
''')
        original = scalar.read_bytes()
        warnings = validate_upload(scalar, 'room/color.usda')
        self.assertEqual([(item['code'], item['layer'], item['path']) for item in warnings],
                         [('usd_display_color_scalar', 'room/color.usda', '/Wall.primvars:displayColor')])
        self.assertEqual(scalar.read_bytes(), original)
        repaired = self.file('repaired.usda', '''#usda 1.0
def Cube "Wall" {
    color3f[] primvars:displayColor = [(0.72, 0.68, 0.62)] (interpolation = "constant")
}
def Shader "Surface" {
    color3f inputs:diffuseColor = (0.72, 0.68, 0.62)
}
''')
        self.assertEqual(validate_upload(repaired), [])

    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_display_color_warning_names_the_usdz_member(self):
        path = self.root / 'room.usdz'
        with ZipFile(path, 'w') as archive:
            archive.writestr('layers/wall.usda', '''#usda 1.0
def Cube "Wall" {
    color3f primvars:displayColor = (0.72, 0.68, 0.62)
}
''')
        warnings = validate_upload(path)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]['layer'], 'layers/wall.usda')
        self.assertEqual(warnings[0]['path'], '/Wall.primvars:displayColor')

    # 验证二进制 USD 以不组合外部依赖的方式检查，仍能拦截越界子层。
    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_binary_usd_is_inspected_without_composition(self):
        from pxr import Sdf
        layer = Sdf.Layer.CreateAnonymous('.usda')
        layer.ImportFromString('#usda 1.0\n( subLayers = [@/etc/private.usda@] )\n')
        path = self.root / 'scene.usdc'
        layer.Export(str(path))
        with self.assertRaises(PermissionError):
            validate_upload(path)

    # 验证 USDZ 不仅检查成员名称，还递归检查成员内容中的依赖路径。
    @unittest.skipUnless(importlib.util.find_spec('pxr'), 'USD parser is not installed')
    def test_usdz_members_are_validated_not_only_archive_names(self):
        path = self.root / 'scene.usdz'
        with ZipFile(path, 'w') as archive:
            archive.writestr('scene.usda', '#usda 1.0\n( subLayers = [@../private.usda@] )\n')
        with self.assertRaises(PermissionError):
            validate_upload(path)
        with ZipFile(path, 'w') as archive:
            archive.writestr('../scene.usda', '#usda 1.0\n')
        with self.assertRaises(PermissionError):
            validate_upload(path)

    # 验证上传使用工作区外的冻结快照，原文件变化不影响快照，退出后删除暂存。
    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative snapshots require Linux')
    def test_upload_snapshot_is_frozen_and_cleaned(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        path = workspace / 'source.urdf'
        path.write_text('<robot name="safe"/>', encoding='utf-8')
        access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(workspace))
        with access_context(access), snapshot_upload(path) as snapshot:
            self.assertFalse(snapshot.is_relative_to(workspace))
            path.write_text('<robot><mesh filename="/etc/private"/></robot>', encoding='utf-8')
            self.assertEqual(snapshot.read_text(), '<robot name="safe"/>')
        self.assertFalse(snapshot.exists())
