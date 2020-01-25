#+
# This Blender addon imports a texture material directly from an
# archive file, such as those downloadable from texturehaven.com
# or cc0textures.com. The archive is expected to contain separate
# images defining maps for diffusion, specular and so on,
# following a common naming convention, one of
#
#     «prefix»_«component»_«n»k.«ext»
#     «prefix»_«component».«ext»
#
# where «prefix» is ignored, «n» (if present) is a decimal integer
# indicating the image resolution, «ext» is some suitable filename
# extension, and «component» is one of the strings listed in the MAP
# enum below.
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
        "version" : (1, 1, 1),
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
    "names of maps that I know how to use. Each value is a tuple" \
    " of alternative names."
    BUMP = ("bump",)
    DIFFUSE = ("col", "diff")
    DISPLACEMENT = "disp"
    NORMAL = ("nor", "normal", "nrm")
    ROUGHNESS = ("rgh", "rough", "roughness")
    SPECULAR = ("met", "spec")
    NONE = "none" # no such map

    @property
    def namestr(self) :
        "the strings that occur in the filename for this particular image map."
        return \
            self.value
    #end namestr

    @property
    def idstr(self) :
        return \
            self.value[0]
    #end idstr

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

@enum.unique
class USE_DISPLACEMENT(enum.Enum) :
    "how to use a loaded displacement map."
    NO = ("no", "No", "don’t use")
    MATERIAL = ("material", "In Material", "use in material nodes")
    TEXTURE = ("texture", "In Texture", "load as separate texture for use in displacement modifier")

    @property
    def idstr(self) :
        return \
            self.value[0]
    #end idstr

    @property
    def label(self) :
        return \
            self.value[1]
    #end label

    @property
    def description(self) :
        return \
            self.value[2]
    #end description

#end USE_DISPLACEMENT

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
    first_priority : bpy.props.EnumProperty \
      (
        name = "First Prefer",
        description = "which map to prefer, among the ones available",
        items =
            (
                (MAP.NONE.idstr, "None", "nothing"),
                (MAP.NORMAL.idstr, "Normal Map", "normal map, if available"),
                (MAP.BUMP.idstr, "Bump Map", "bump map, if available"),
                (MAP.DISPLACEMENT.idstr, "Displacement Map", "displacement map, if available"),
            ),
        default = MAP.NORMAL.idstr,
      )
    second_priority : bpy.props.EnumProperty \
      (
        name = "Then Prefer",
        description = "which map to prefer next, among the ones available",
        items =
            (
                (MAP.NONE.idstr, "None", "nothing"),
                (MAP.NORMAL.idstr, "Normal Map", "normal map, if available"),
                (MAP.BUMP.idstr, "Bump Map", "bump map, if available"),
                (MAP.DISPLACEMENT.idstr, "Displacement Map", "displacement map, if available"),
            ),
        default = MAP.BUMP.idstr,
      )
    third_priority : bpy.props.EnumProperty \
      (
        name = "Use Last",
        description = "which map to use last, among the ones available",
        items =
            (
                (MAP.NONE.idstr, "None", "nothing"),
                (MAP.NORMAL.idstr, "Normal Map", "normal map, if available"),
                (MAP.BUMP.idstr, "Bump Map", "bump map, if available"),
                (MAP.DISPLACEMENT.idstr, "Displacement Map", "displacement map, if available"),
            ),
        default = MAP.DISPLACEMENT.idstr,
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
        description = "how to use displacement map, if available",
        items = tuple
            (
                (m.idstr, m.label, m.description)
                for m in USE_DISPLACEMENT.__members__.values()
            ),
        default = USE_DISPLACEMENT.MATERIAL.idstr
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
                (n, MAP[k])
                for k, m in MAP.__members__.items()
                if k != MAP.NONE.value
                for n in m.namestr
              )
            map_preference = tuple \
              (
                by_namestr[k]
                for k in (self.first_priority, self.second_priority, self.third_priority)
                if
                        k != MAP.NONE.idstr
                    and
                        (
                            k != MAP.DISPLACEMENT.idstr
                        or
                            self.use_displacement != USE_DISPLACEMENT.NO.idstr
                        )
              )
              # Note it doesn’t matter if user chooses duplicates, as only
              # the first occurrence of each map type has effect.
            load_component = \
                {
                    MAP.DIFFUSE : self.use_diffuse,
                    MAP.SPECULAR : self.use_specular,
                    MAP.NORMAL : MAP.NORMAL in map_preference,
                    MAP.BUMP : MAP.BUMP in map_preference,
                    MAP.ROUGHNESS : self.use_roughness,
                    MAP.DISPLACEMENT : MAP.DISPLACEMENT in map_preference,
                }
            components = {}
            name_pat = r"^.+_([a-zA-Z]+)(?:_(\d+)k)?\..+$"
            img_size = None
            for item in os.listdir(temp_dir) :
                match = re.search(name_pat, item)
                if match != None :
                    namestr = match.group(1).lower()
                    this_size = match.group(2)
                    if this_size != None :
                        this_size = int(this_size) * 1024
                        if img_size == None :
                            img_size = this_size
                        else :
                            assert this_size == img_size
                        #end if
                    #end if
                    if namestr in by_namestr :
                        map = by_namestr[namestr]
                        if load_component.get(map, False) :
                            components[map] = (namestr, os.path.join(temp_dir, item))
                        #end if
                    #end if
                #end if
            #end for
            if len(components) == 0 :
                raise Failure("No suitable texture components found.")
            #end if
            map_preference = tuple(k for k in map_preference if k in components)
            if len(map_preference) != 0 :
                map_preference = map_preference[0]
            else :
                map_preference = None
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
            map_location = [-100, 200]

            def load_image(map) :
                image = bpy.data.images.load(components[map][1])
                namestr = components[map][0]
                image.name = "%s_%s" % (material_name, namestr)
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
                bump_convert.location = extra_nodes_location
                material_tree.links.new \
                  (
                    texture_output,
                    bump_convert.inputs["Height"]
                  )
                return \
                    bump_convert.outputs["Normal"]
            #end add_bump_convert_nodes

            def add_normal_mapping_nodes(texture_output, extra_nodes_location) :
                # adds a node for controlling the strength of the normal map.
                map = material_tree.nodes.new("ShaderNodeNormalMap")
                map.location = extra_nodes_location
                material_tree.links.new \
                  (
                    texture_output,
                    map.inputs["Color"]
                  )
                return \
                    map.outputs["Normal"]
            #end add_normal_mapping_nodes

            add_special_nodes_for = \
                {
                    MAP.BUMP : add_bump_convert_nodes,
                    MAP.NORMAL : add_normal_mapping_nodes,
                }
            for map in (
                    (MAP.DIFFUSE, MAP.SPECULAR, MAP.ROUGHNESS)
                +
                    ((), (map_preference,))
                        [map_preference != None and map_preference != MAP.DISPLACEMENT]
            ) :
              # Go according to ordering of input nodes on Principled BSDF,
              # to avoid wires crossing.
              # Also note MAP.DISPLACEMENT handled specially below.
                if map in components :
                    extra_nodes_location = list(map_location)
                    extra_nodes_location[0] += 300
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
            if (
                    map_preference == MAP.DISPLACEMENT
                and
                    self.use_displacement == USE_DISPLACEMENT.MATERIAL.idstr
            ) :
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
            if (
                    map_preference == MAP.DISPLACEMENT
                and
                    self.use_displacement == USE_DISPLACEMENT.TEXTURE.idstr
            ) :
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
