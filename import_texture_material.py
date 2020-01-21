#+
# This Blender addon imports a texture material directly from an
# archive file, such as those downloadable from texturehaven.com. The
# archive is expected to contain separate images defining maps for
# diffusion, specular and so on, following a common naming convention,
# as follows:
#
#     «prefix»_«component»_«n»k.«ext»
#
# where «prefix» is ignored, «n» is a decimal integer indicating
# the image resolution, «ext» is some suitable filename extension,
# and «component» is one of the strings listed in the MAP enum below.
#
# These will be extracted and packed into the .blend file, and used
# in a new material definition named according to the archive file name.
#
# External program needed: unar.
#
# Copyright 2020 by Lawrence D'Oliveiro <ldo@geek-central.gen.nz>.
# Licensed under CC-BY-SA <http://creativecommons.org/licenses/by-sa/4.0/>.
#-

import sys
import os
import enum
import re
import subprocess
import tempfile
import shutil
import bpy
from bpy.props import \
    CollectionProperty, \
    StringProperty
import bpy_extras.io_utils

bl_info = \
    {
        "name" : "Import Texture Material",
        "author" : "Lawrence D'Oliveiro <ldo@geek-central.gen.nz>",
        "version" : (0, 3, 0),
        "blender" : (2, 81, 0),
        "location" : "File > Import",
        "description" : "imports a complete texture material from an archive file.",
        "warning" : "",
        "wiki_url" : "",
        "tracker_url" : "",
        "category" : "Import-Export",
    }

#+
# Useful stuff
#-

class Failure(Exception) :

    def __init__(self, msg) :
        self.msg = msg
    #end __init__

#end Failure

def deselect_all(material_tree) :
    for node in material_tree.nodes :
        node.select = False
    #end for
#end deselect_all

#+
# Do the work
#-

@enum.unique
class MAP(enum.Enum) :
    "names of maps that I know how to use."
    BUMP = "bump"
    DIFFUSE = "diff"
    DISPLACEMENT = "disp"
    NORMAL = "nor"
    ROUGHNESS = "rough"
    SPECULAR = "spec"

    @property
    def namestr(self) :
        "the string that occurs in the filename for this particular image map."
        return \
            self.value
    #end namestr

    @property
    def is_colour(self) :
        "does this component actually represent colour data."
        return \
            self in {MAP.DIFFUSE, MAP.SPECULAR}
    #end is_colour

    @property
    def principled_bsdf_input_name(self) :
      # names of principled shader inputs are ['Base Color', 'Subsurface', 'Subsurface Radius',
      # 'Subsurface Color', 'Metallic', 'Specular', 'Specular Tint', 'Roughness',
      # 'Anisotropic', 'Anisotropic Rotation', 'Sheen', 'Sheen Tint', 'Clearcoat',
      # 'Clearcoat Roughness', 'IOR', 'Transmission', 'Transmission Roughness',
      # 'Emission', 'Alpha', 'Normal', 'Clearcoat Normal', 'Tangent']
        return \
            {
                MAP.DIFFUSE : "Base Color",
                MAP.ROUGHNESS : "Roughness",
                MAP.NORMAL : "Normal",
                MAP.SPECULAR : "Specular",
            }[self]
    #end principled_bsdf_input_name

#end MAP

class ImportTextureMaterial(bpy.types.Operator, bpy_extras.io_utils.ImportHelper) :
    bl_idname = "material.import_texture"
    bl_label = "Import Texture Material"

    files : CollectionProperty \
      (
        name = "File Path",
        description = "Texture Archive File",
        type = bpy.types.OperatorFileListElement
      )

    def execute(self, context) :
        temp_dir = None
        try :
            temp_dir = tempfile.mkdtemp(prefix = "texture-import-")
            subprocess.check_call \
              (
                args = ("unar", "-q", "-D", os.path.abspath(self.filepath)),
                  # interesting that “-qD” isn’t accepted...
                cwd = temp_dir
              )
            by_namestr = dict \
              (
                (m.namestr, MAP[k]) for k, m in MAP.__members__.items()
              )
            components = {}
            name_pat = r"^.+_([a-zA-Z]+)_(\d+)k\..+$"
            img_size = None
            for item in os.listdir(temp_dir) :
                match = re.search(name_pat, item)
                if match != None :
                    namestr = match.group(1).lower()
                    this_size = int(match.group(2)) * 1024
                    if img_size == None :
                        img_size = this_size
                    else :
                        assert this_size == img_size
                    #end if
                    if namestr in by_namestr :
                        components[by_namestr[namestr]] = os.path.join(temp_dir, item)
                    #end if
                #end if
            #end for
            sys.stderr.write("found components: %s\n" % repr(components)) # debug
            material_name = os.path.splitext(os.path.basename(self.filepath))[0]
            material = bpy.data.materials.new(material_name)
            # material.diffuse_color?
            material.use_nodes = True
            material_tree = material.node_tree
            for node in material_tree.nodes :
              # clear out default nodes
                material_tree.nodes.remove(node)
            #end for
            tex_coords = material_tree.nodes.new("ShaderNodeTexCoord")
            tex_coords.location = (-400, 0)
            fanout = material_tree.nodes.new("NodeReroute")
            fanout.location = (-200, -150)
            material_tree.links.new(tex_coords.outputs["UV"], fanout.inputs[0])
              # fanout makes it easy to change this coordinate source for all
              # texture components at once
            main_shader = material_tree.nodes.new("ShaderNodeBsdfPrincipled")
            main_shader.location = (400, 0)
            map_location = [0, 200]

            def new_map_image(map) :
                image = bpy.data.images.load(components[map])
                image.name = "%s_%s" % (material_name, map.namestr)
                if not map.is_colour :
                    image.colorspace_settings.name = "Non-Color"
                #end if
                image.pack()
                tex_image = material_tree.nodes.new("ShaderNodeTexImage")
                tex_image.image = image
                tex_image.location = tuple(map_location)
                material_tree.links.new(tex_image.inputs[0], fanout.outputs[0])
                map_location[1] -= 300
                return \
                    tex_image
            #end new_map_image

            for map in (MAP.DIFFUSE, MAP.SPECULAR, MAP.ROUGHNESS, MAP.NORMAL) :
              # Go according to ordering of input nodes on Principled BSDF,
              # to avoid wires crossing.
              # Also note MAP.DISPLACEMENT handled specially below.
                if map in components :
                    tex_image = new_map_image(map)
                    material_tree.links.new \
                      (
                        tex_image.outputs["Color"],
                        main_shader.inputs[map.principled_bsdf_input_name]
                      )
                #end for
            #end for
            material_output = material_tree.nodes.new("ShaderNodeOutputMaterial")
            material_output.location = (750, 0)
            material_tree.links.new(main_shader.outputs[0], material_output.inputs[0])
            if MAP.DISPLACEMENT in components :
                tex_image = new_map_image(MAP.DISPLACEMENT)
                material_tree.links.new \
                  (
                    tex_image.outputs["Color"],
                    material_output.inputs["Displacement"]
                  )
            #end if
            deselect_all(material_tree)
            # all done
            status = {"FINISHED"}
        except Failure as why :
            sys.stderr.write("Failure: %s\n" % why.msg) # debug
            self.report({"ERROR"}, why.msg)
            status = {"CANCELLED"}
        finally :
            if temp_dir != None :
                try :
                    shutil.rmtree(temp_dir)
                except OSError as err :
                    sys.stderr.write("removing temp dir: " + repr(err) + "\n") # debug
                #end try
            #end if
        #end try
        return \
            status
    #end execute

#end ImportTextureMaterial

#+
# Mainline
#-

def add_invoke_item(self, context) :
    self.layout.operator(ImportTextureMaterial.bl_idname, text = "Texture Material")
#end add_invoke_item

_classes_ = \
    (
        ImportTextureMaterial,
    )

def register() :
    for ċlass in _classes_ :
        bpy.utils.register_class(ċlass)
    #end for
    bpy.types.TOPBAR_MT_file_import.append(add_invoke_item)
#end register

def unregister() :
    bpy.types.TOPBAR_MT_file_import.remove(add_invoke_item)
    for ċlass in _classes_ :
        bpy.utils.unregister_class(ċlass)
    #end for
#end unregister

if __name__ == "__main__" :
    register()
#end if
