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
import bpy.props
import bpy_extras.io_utils

bl_info = \
    {
        "name" : "Import Texture Material",
        "author" : "Lawrence D'Oliveiro <ldo@geek-central.gen.nz>",
        "version" : (0, 9, 1),
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
                MAP.BUMP : "Normal", # after putting through a Bump node
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

    files : bpy.props.CollectionProperty \
      (
        name = "File Path",
        description = "Texture Archive File",
        type = bpy.types.OperatorFileListElement
      )
    use_diffuse : bpy.props.BoolProperty \
      (
        name = "Diffuse Colour",
        description = "use diffuse colour in material, if available",
        default = True
      )
    use_specular : bpy.props.BoolProperty \
      (
        name = "Specular Colour",
        description = "use specular colour in material, if available",
        default = True
      )
    use_normals : bpy.props.BoolProperty \
      (
        name = "Normal Map",
        description = "use normal map in material, if available",
        default = True
      )
    use_bump : bpy.props.BoolProperty \
      (
        name = "Bump Map",
        description = "use bump map in material, if available",
        default = True
      )
    use_roughness : bpy.props.BoolProperty \
      (
        name = "Roughness Map",
        description = "use roughness map in material, if available",
        default = True
      )
    use_displacement : bpy.props.EnumProperty \
      (
        name = "Use Displacement",
        description = "use displacement map, if available",
        items =
            (
                ("no", "No", "don’t use displacement map"),
                ("material", "In Material", "as part of material definition"),
                ("texture", "In Texture",
                    "as part of separate texture definition, for use in displacement modifier"),
            ),
        default = "material",
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
            load_component = \
                {
                    MAP.DIFFUSE : self.use_diffuse,
                    MAP.SPECULAR : self.use_specular,
                    MAP.NORMAL : self.use_normals,
                    MAP.BUMP : self.use_bump,
                    MAP.ROUGHNESS : self.use_roughness,
                    MAP.DISPLACEMENT : self.use_displacement != "no",
                }
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
                        map = by_namestr[namestr]
                        if load_component.get(map, False) :
                            components[map] = os.path.join(temp_dir, item)
                        #end if
                    #end if
                #end if
            #end for
            if MAP.NORMAL in components :
                # prefer normal over bump map
                components.pop(MAP.BUMP, None)
            #end if
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
            tex_coords.location = (-600, 0)
            tex_mapping = material_tree.nodes.new("ShaderNodeMapping")
            tex_mapping.location = (-400, 0)
            material_tree.links.new(tex_coords.outputs["UV"], tex_mapping.inputs["Vector"])
            fanout = material_tree.nodes.new("NodeReroute")
            fanout.location = (-200, -150)
            material_tree.links.new(tex_mapping.outputs["Vector"], fanout.inputs[0])
              # fanout makes it easy to change this coordinate source for all
              # texture components at once
            main_shader = material_tree.nodes.new("ShaderNodeBsdfPrincipled")
            main_shader.location = (500, 0)
            map_location = [0, 200]

            def load_image(map) :
                image = bpy.data.images.load(components[map])
                image.name = "%s_%s" % (material_name, map.namestr)
                if not map.is_colour :
                    image.colorspace_settings.name = "Non-Color"
                #end if
                image.pack()
                return \
                    image
            #end load_image

            def new_image_texture_node(map) :
                image = load_image(map)
                tex_image = material_tree.nodes.new("ShaderNodeTexImage")
                tex_image.image = image
                tex_image.location = tuple(map_location)
                material_tree.links.new(tex_image.inputs[0], fanout.outputs[0])
                map_location[1] -= 300
                return \
                    tex_image
            #end new_image_texture_node

            def add_bump_convert_nodes(texture_output, extra_nodes_location) :
                # adds nodes for converting a bump map to a normal map.
                bump_convert = material_tree.nodes.new("ShaderNodeBump")
                bump_convert.location = (extra_nodes_location[0] + 300, extra_nodes_location[1])
                material_tree.links.new \
                  (
                    texture_output,
                    bump_convert.inputs["Height"]
                  )
                return \
                    bump_convert.outputs["Normal"]
            #end add_bump_convert_nodes

            normal_mapping_group_name = "texture_material_normal_mapping"

            def add_normal_mapping_nodes(texture_output, extra_nodes_location) :
                # adds nodes for controlling the strength of the normal map.
                nonlocal normal_mapping_group_name
                group = None # to begin with
                if normal_mapping_group_name in bpy.data.node_groups :
                    group = bpy.data.node_groups[normal_mapping_group_name]
                    if (
                            len(group.inputs) == 1
                        and
                            len(group.outputs) == 1
                        and
                            group.inputs[0].type == "VECTOR"
                        and
                            group.outputs[0].type == "VECTOR"
                    ) :
                        pass # looks OK
                    else :
                        # these are not the nodes you’re looking for
                        group = None
                    #end if
                #end if
                if group == None :
                    # existing node group not found, create a new one
                    group = bpy.data.node_groups.new \
                      (
                        normal_mapping_group_name,
                        "ShaderNodeTree"
                      )
                    normal_mapping_group_name = group.name
                      # for finding it next time
                    group_input = group.nodes.new("NodeGroupInput")
                    group_input.location = (-400, 200)
                    group_output = group.nodes.new("NodeGroupOutput")
                    group_output.location = (400, 0)
                    # Note that there is no point calling group_input.outputs.new(),
                    # group_output.inputs.new(), group.inputs.new() or
                    # group.outputs.new()--these calls do nothing. Instead, all inputs and
                    # outputs for the node group are automatically created as connections
                    # are made to them via group.links.new().
                    #
                    # Basic vector-rotation formula: given unit vectors V₀ and V₁
                    # such that the relative rotation from V₀ to V₁ is to be applied
                    # to a vector V to produce a new vector V’, the formula is
                    #
                    #     V’ = (V₀ × V₁) × V + (V₀ ⋅ V₁)V
                    #
                    zvector = group.nodes.new("ShaderNodeCombineXYZ") # V₀
                    zvector.location = (-400, 0)
                    zvector.inputs[0].default_value = 0
                    zvector.inputs[1].default_value = 0
                    zvector.inputs[2].default_value = 1
                    cross1 = group.nodes.new("ShaderNodeVectorMath")
                    cross1.location = (-200, 0)
                    cross1.operation = "CROSS_PRODUCT"
                    dot = group.nodes.new("ShaderNodeVectorMath")
                    dot.location = (-200, -200)
                    dot.operation = "DOT_PRODUCT"
                    group.links.new(zvector.outputs[0], cross1.inputs[0]) # V₀
                    group.links.new(group_input.outputs[0], cross1.inputs[1]) # V₁
                    group.links.new(zvector.outputs[0], dot.inputs[0]) # V₀
                    group.links.new(group_input.outputs[0], dot.inputs[1]) # V₁
                    geometry = group.nodes.new("ShaderNodeNewGeometry")
                    geometry.location = (-200, -400)
                    cross2 = group.nodes.new("ShaderNodeVectorMath")
                    cross2.location = (0, 0)
                    cross2.operation = "CROSS_PRODUCT"
                    group.links.new(cross1.outputs[0], cross2.inputs[0])
                    group.links.new(geometry.outputs["Normal"], cross2.inputs[1]) # V
                    scale = group.nodes.new("ShaderNodeVectorMath")
                    scale.location = (0, -200)
                    scale.operation = "SCALE"
                    group.links.new(geometry.outputs["Normal"], scale.inputs[0]) # V
                    group.links.new(dot.outputs[1], scale.inputs[2]) # note not dot.outputs[0] or scale.inputs[1]!
                    add = group.nodes.new("ShaderNodeVectorMath")
                    add.location = (200, 0)
                    add.operation = "ADD"
                    group.links.new(cross2.outputs[0], add.inputs[0])
                    group.links.new(scale.outputs[0], add.inputs[1])
                    group.links.new(add.outputs[0], group_output.inputs[0])
                    # have to assign names to nodegroup sockets this way:
                    group.inputs[0].name = "In"
                    group.outputs[0].name = "Out"
                    deselect_all(group)
                #end if
                normal_strength = material_tree.nodes.new("ShaderNodeGroup")
                normal_strength.node_tree = group
                normal_strength.location = (extra_nodes_location[0] + 300, extra_nodes_location[1])
                material_tree.links.new \
                  (
                    texture_output,
                    normal_strength.inputs[0]
                  )
                return \
                    normal_strength.outputs[0]
            #end add_normal_mapping_nodes

            add_special_nodes_for = \
                {
                    MAP.BUMP : add_bump_convert_nodes,
                    MAP.NORMAL : add_normal_mapping_nodes,
                }
            for map in (MAP.DIFFUSE, MAP.SPECULAR, MAP.ROUGHNESS, MAP.NORMAL, MAP.BUMP) :
              # Go according to ordering of input nodes on Principled BSDF,
              # to avoid wires crossing.
              # Also note MAP.DISPLACEMENT handled specially below.
                if map in components :
                    extra_nodes_location = list(map_location)
                    tex_image = new_image_texture_node(map)
                    output_terminal = tex_image.outputs["Color"]
                    add_special_nodes = add_special_nodes_for.get(map)
                    if add_special_nodes != None :
                        output_terminal = add_special_nodes(output_terminal, extra_nodes_location)
                    #end if
                    material_tree.links.new \
                      (
                        output_terminal,
                        main_shader.inputs[map.principled_bsdf_input_name]
                      )
                #end for
            #end for
            material_output = material_tree.nodes.new("ShaderNodeOutputMaterial")
            material_output.location = (850, 0)
            material_tree.links.new(main_shader.outputs[0], material_output.inputs[0])
            if self.use_displacement == "material" and MAP.DISPLACEMENT in components :
                tex_image = new_image_texture_node(MAP.DISPLACEMENT)
                material_tree.links.new \
                  (
                    tex_image.outputs["Color"],
                    material_output.inputs["Displacement"]
                  )
                material.cycles.displacement_method = "BOTH"
                  # values are "BUMP" (default), "DISPLACEMENT" or "BOTH"
            #end if
            deselect_all(material_tree)
            if self.use_displacement == "texture" and MAP.DISPLACEMENT in components :
                image = load_image(MAP.DISPLACEMENT)
                tex = bpy.data.textures.new(image.name, "IMAGE")
                tex.image = image
            #end if
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
