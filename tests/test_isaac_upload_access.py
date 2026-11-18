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
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def file(self, name, text):
        path = self.root / name
        path.write_text(text, encoding='utf-8')
        return path

    def test_urdf_mesh_and_texture_references_are_checked(self):
        for tag in ('mesh', 'texture'):
            for reference in ('/etc/private', '../other-asset/file', 'file:///tmp/x', 'package://other/mesh'):
                with self.subTest(tag=tag, reference=reference):
                    path = self.file('robot.urdf', f'<robot name="r"><{tag} filename="{reference}"/></robot>')
                    with self.assertRaises(PermissionError):
                        validate_upload(path)
        validate_upload(self.file('valid.urdf', '<robot name="r"><mesh filename="meshes/part.stl"/><texture filename="textures/a.png"/></robot>'))

    def test_mjcf_includes_and_compiler_directories_are_checked(self):
        for fragment in ('<include file="../other.xml"/>', '<compiler meshdir="/etc"/>',
                         '<compiler texturedir="file:///etc"/>', '<texture file="../secret.png"/>'):
            with self.subTest(fragment=fragment), self.assertRaises(PermissionError):
                validate_upload(self.file('robot.xml', '<mujoco>' + fragment + '</mujoco>'))
        validate_upload(self.file('valid.xml', '<mujoco><compiler meshdir="mesh"/><include file="parts/arm.xml"/></mujoco>'))

    def test_xml_entities_are_not_accepted(self):
        with self.assertRaises(ValueError):
            validate_upload(self.file('r.urdf', '<!DOCTYPE robot [<!ENTITY value "x">]><robot name="&value;"/>'))

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

    def test_wavefront_file_references_and_commands_are_checked(self):
        for suffix, contents in (('.obj', 'mtllib ../private.mtl'), ('.obj', 'call /etc/private'),
                                 ('.mtl', 'map_Kd /etc/private.png'), ('.mtl', 'bump -s 1 1 1 ../private.png')):
            with self.subTest(suffix=suffix, contents=contents), self.assertRaises(PermissionError):
                validate_upload(self.file('mesh' + suffix, contents))
        validate_upload(self.file('good.obj', 'mtllib materials/material.mtl\nv 0 0 0'))
        validate_upload(self.file('good.mtl', 'map_Kd -s 1 1 1 textures/color.png'))

    def test_remote_filename_controls_parser_and_cannot_traverse(self):
        path = self.file('pretend.bin', '<robot><texture filename="/etc/private"/></robot>')
        with self.assertRaises(PermissionError):
            validate_upload(path, 'robot.urdf')
        for name in ('../foreign/source.bin', '/tmp/source.bin', 'C:source.bin'):
            with self.subTest(name=name), self.assertRaises(PermissionError):
                validate_upload(path, name)
        with self.assertRaises(ValueError):
            validate_upload(path, 'unknown.fbx')

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
    def test_binary_usd_is_inspected_without_composition(self):
        from pxr import Sdf
        layer = Sdf.Layer.CreateAnonymous('.usda')
        layer.ImportFromString('#usda 1.0\n( subLayers = [@/etc/private.usda@] )\n')
        path = self.root / 'scene.usdc'
        layer.Export(str(path))
        with self.assertRaises(PermissionError):
            validate_upload(path)

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
