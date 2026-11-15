bl_info = {
    "name": "BBUtils",
    "author": "Budloks",
    "version": (1, 16, 0),
    "blender": (5, 2, 0),
    "location": "View3D > Sidebar > BBUtils Tab  |  Dope Sheet > Sidebar > BBUtils  |  Graph Editor > Sidebar > BBUtils",
    "description": "Batch viewport rendering, animation tools, EzPhyzi throw physics, armature constraint utilities, and controller bone tools",
    "category": "Animation",
}

import bpy
import bmesh
import gpu
import math
import time
import os
import zlib
from pathlib import Path
from bpy.app.handlers import persistent
from bpy.types import Panel, Operator, PropertyGroup, Menu, AddonPreferences
from bpy.props import (BoolProperty, IntProperty, StringProperty, FloatProperty,
                       CollectionProperty,
                       PointerProperty, EnumProperty, FloatVectorProperty)
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Quaternion, Vector


# ─────────────────────────────────────────────────────────────────────────────
#  UTILITY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _redraw(context, area_types):
    for area in context.screen.areas:
        if area.type in area_types:
            area.tag_redraw()


def _action_owner_map():
    """Return {action: object} mapping each action to its current owner object."""
    mapping = {}
    for obj in bpy.data.objects:
        if obj.animation_data and obj.animation_data.action:
            action = obj.animation_data.action
            if action not in mapping:
                mapping[action] = obj
    return mapping


def _ensure_animation_data(obj):
    if not obj.animation_data:
        obj.animation_data_create()
    return obj.animation_data


def _draw_hint(layout, *lines):
    """Render small subdued hint lines under a button."""
    col = layout.column(align=False)
    col.scale_y = 0.65
    for line in lines:
        col.label(text=line, icon='NONE')


_WIDGET_COLLECTION_NAME = "BBUtils Widgets"

# Display label + internal name for each built-in preset (order = UI order)
_SHAPE_PRESETS = [
    ('OCTAHEDRAL', "Octahedral", "Classic Blender bone diamond shape"),
    ('STICK',      "Stick",      "Thin stick spanning the full bone"),
    ('BBONE',      "B-Bone",     "Boxy shape representing a bendy-bone segment"),
    ('ENVELOPE',   "Envelope",   "Rounded capsule-like envelope shape"),
    ('WIRE',       "Wire",       "Wireframe-only octahedral shape (edges, no faces)"),
    ('SPHERE',     "Sphere",     "Plain sphere widget"),
    ('CUBE',       "Cube",       "Plain cube widget"),
    ('CIRCLE',     "Circle",     "Flat ring widget"),
    ('CUSTOM',     "Custom Object", "Use the object picked below"),
]


def _get_or_create_widget_collection(context):
    """Hidden collection that stores auto-generated preset bone-shape meshes."""
    coll = bpy.data.collections.get(_WIDGET_COLLECTION_NAME)
    if not coll:
        coll = bpy.data.collections.new(_WIDGET_COLLECTION_NAME)
        context.scene.collection.children.link(coll)
    if coll.name not in context.scene.collection.children:
        try:
            context.scene.collection.children.link(coll)
        except Exception:
            pass
    coll.hide_render = True
    return coll


def _build_octahedral_shape(bm, wire_only=False, waist=0.1, width=0.1):
    """Blender's classic bone diamond: pinched waist near the head, tapering
    out to the tail. wire_only=True builds edges only (no faces) => 'Wire'."""
    v_head = bm.verts.new((0.0, 0.0, 0.0))
    v_tail = bm.verts.new((0.0, 1.0, 0.0))
    ring = [
        bm.verts.new((width, waist, 0.0)),
        bm.verts.new((0.0, waist, width)),
        bm.verts.new((-width, waist, 0.0)),
        bm.verts.new((0.0, waist, -width)),
    ]
    if wire_only:
        for v in ring:
            bm.edges.new((v_head, v))
            bm.edges.new((v_tail, v))
        for i in range(4):
            bm.edges.new((ring[i], ring[(i + 1) % 4]))
    else:
        for i in range(4):
            a, b = ring[i], ring[(i + 1) % 4]
            bm.faces.new((v_head, a, b))
            bm.faces.new((v_tail, b, a))


def _build_box_shape(bm, half_width):
    """A simple box spanning the bone from head (y=0) to tail (y=1)."""
    hw = half_width
    bottom = [(-hw, 0.0, -hw), (hw, 0.0, -hw), (hw, 0.0, hw), (-hw, 0.0, hw)]
    top    = [(-hw, 1.0, -hw), (hw, 1.0, -hw), (hw, 1.0, hw), (-hw, 1.0, hw)]
    vb = [bm.verts.new(p) for p in bottom]
    vt = [bm.verts.new(p) for p in top]
    bm.faces.new(vb[::-1])
    bm.faces.new(vt)
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((vb[i], vb[j], vt[j], vt[i]))


def _get_or_create_widget(context, preset):
    """Return (creating if needed) a small mesh object used as a bone custom
    shape, matching Blender's own bone display styles. Cached by name."""
    name = f"WGT-BBUtils_{preset}"
    existing = bpy.data.objects.get(name)
    if existing:
        return existing

    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()

    if preset == 'OCTAHEDRAL':
        _build_octahedral_shape(bm, wire_only=False)
    elif preset == 'WIRE':
        _build_octahedral_shape(bm, wire_only=True)
    elif preset == 'STICK':
        _build_box_shape(bm, half_width=0.015)
    elif preset == 'BBONE':
        _build_box_shape(bm, half_width=0.08)
    elif preset == 'ENVELOPE':
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.3)
        for v in bm.verts:
            v.co.y = v.co.y * 1.7 + 0.5
    elif preset == 'SPHERE':
        bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.4)
        for v in bm.verts:
            v.co.y += 0.5
    elif preset == 'CUBE':
        bmesh.ops.create_cube(bm, size=0.6)
        for v in bm.verts:
            v.co.y += 0.5
    elif preset == 'CIRCLE':
        bmesh.ops.create_circle(bm, cap_ends=False, segments=24, radius=0.3)
        for v in bm.verts:
            v.co.y += 0.5
    else:
        _build_octahedral_shape(bm, wire_only=False)

    bm.to_mesh(mesh)
    bm.free()
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    obj.hide_render = True
    obj.hide_select = True
    coll = _get_or_create_widget_collection(context)
    coll.objects.link(obj)
    return obj


# ── Action / slot helpers ────────────────────────────────────────────────────
# Blender 4.4+ uses "layered" actions: F-Curves live in channelbags, one
# channelbag per *slot* (a slot = "the part of the action that animates
# object X"). Blender 5.0 removed Action.fcurves, and
# Action.fcurve_ensure_for_datablock() only works on the action that is
# CURRENTLY ASSIGNED to the object - it raises for every other action. So
# whenever we touch more than the active action we go through slots and
# channelbags directly.

def _is_layered(action):
    return hasattr(action, "slots") and hasattr(action, "layers")


def _slot_for_obj(action, obj):
    """Return the slot of `action` that animates `obj`, or None."""
    if not _is_layered(action):
        return None
    ad = obj.animation_data
    if ad and ad.action == action:
        slot = getattr(ad, "action_slot", None)
        if slot is not None:
            return slot
    ident = "OB" + obj.name
    for slot in action.slots:
        if slot.identifier == ident:
            return slot
    last = getattr(ad, "last_slot_identifier", "") if ad else ""
    if last:
        for slot in action.slots:
            if slot.identifier == last:
                return slot
    if len(action.slots) == 1:
        return action.slots[0]
    return None


def _get_action_fcurves(action, obj=None):
    """List the F-Curves of an action. Pass `obj` to only get the curves of
    the slot that animates that object (recommended - an action can hold
    animation for several objects)."""
    if hasattr(action, "fcurves"):          # legacy actions (Blender <= 4.3)
        return list(action.fcurves)

    curves = []
    slot = None
    if obj is not None:
        slot = _slot_for_obj(action, obj)
        if slot is None:
            return curves
    try:
        for layer in action.layers:
            for strip in layer.strips:
                if strip.type != 'KEYFRAME':
                    continue
                if slot is not None:
                    cb = strip.channelbag(slot)
                    bags = [cb] if cb else []
                else:
                    bags = list(strip.channelbags)
                for cb in bags:
                    curves.extend(cb.fcurves)
    except Exception:
        pass
    return curves


def _ensure_fcurve(action, obj, data_path, index=0, group=""):
    """Find or create an F-Curve for `obj` inside ANY action (assigned or not)."""
    if not _is_layered(action):             # legacy actions (Blender <= 4.3)
        for fc in action.fcurves:
            if fc.data_path == data_path and fc.array_index == index:
                return fc
        return action.fcurves.new(data_path, index=index)

    slot = _slot_for_obj(action, obj)
    if slot is None:
        slot = action.slots.new(id_type='OBJECT', name=obj.name)
        ad = obj.animation_data
        if ad and ad.action == action and getattr(ad, "action_slot", None) is None:
            ad.action_slot = slot
    layer = action.layers[0] if len(action.layers) else action.layers.new("Layer")
    strip = layer.strips[0] if len(layer.strips) else layer.strips.new(type='KEYFRAME')
    cb = strip.channelbag(slot, ensure=True)
    fc = cb.fcurves.find(data_path, index=index)
    if fc is None:
        fc = cb.fcurves.new(data_path, index=index, group_name=group)
    return fc


def _remove_fcurve(action, fc):
    """Remove an F-Curve from an action (works for layered and legacy actions)."""
    if hasattr(action, "fcurves"):
        action.fcurves.remove(fc)
        return True
    for layer in action.layers:
        for strip in layer.strips:
            if strip.type != 'KEYFRAME':
                continue
            for cb in strip.channelbags:
                for other in cb.fcurves:
                    if other == fc:
                        cb.fcurves.remove(fc)
                        return True
    return False


def _set_key(fc, frame, value):
    """Insert a key, replacing an existing key on that exact frame."""
    fc.keyframe_points.insert(frame, value)
    if hasattr(fc, "update"):
        fc.update()


def _rig_actions(obj):
    """Return (actions, skipped_count): every action in the file that can
    animate `obj`. Actions that only hold slots for OTHER objects are skipped."""
    ad = obj.animation_data
    active = ad.action if ad else None
    result, skipped = [], 0
    for act in bpy.data.actions:
        if active is not None and act == active:
            result.append(act)
        elif not _is_layered(act):
            result.append(act)
        elif act.slots and not any(s.target_id_type in {'OBJECT', 'UNSPECIFIED'}
                                   for s in act.slots):
            skipped += 1        # e.g. a material / shape-key action
        elif len(act.slots) == 0 or _slot_for_obj(act, obj) is not None:
            result.append(act)
        else:
            skipped += 1
    return result, skipped


def _assign_action(obj, action):
    """Assign an action AND the right slot to an object (assigning only the
    action leaves the slot empty when slot names differ -> no animation)."""
    ad = _ensure_animation_data(obj)
    ad.action = action
    if action is not None and hasattr(ad, "action_slot"):
        slot = _slot_for_obj(action, obj)
        if slot is not None and ad.action_slot != slot:
            ad.action_slot = slot
    return ad


def _selected_bone_names(obj):
    """Names of selected bones, in armature order (Blender 4.x and 5.x:
    Bone.select was removed in 5.0, selection now lives on PoseBone)."""
    if obj.mode == 'EDIT':
        names = [b.name for b in obj.data.edit_bones if b.select]
    else:
        pbs = obj.pose.bones
        if len(pbs) and "select" in pbs[0].bl_rna.properties:
            names = [pb.name for pb in pbs if pb.select]
        else:
            names = [b.name for b in obj.data.bones if b.select]
    return names


# ─────────────────────────────────────────────────────────────────────────────
#  PROPERTY GROUPS
# ─────────────────────────────────────────────────────────────────────────────

class BBUtilsBatchRenderProperties(PropertyGroup):
    output_folder: StringProperty(
        name="Output Folder", subtype='DIR_PATH', default=""
    )
    resolution_x: IntProperty(name="X", default=1920, min=1, max=10000)
    resolution_y: IntProperty(name="Y", default=1080, min=1, max=10000)
    resolution_percentage: IntProperty(
        name="Scale", default=100, min=1, max=100, subtype='PERCENTAGE'
    )
    armature_object: PointerProperty(
        name="Armature", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE'
    )
    camera_object: PointerProperty(
        name="Camera", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'CAMERA'
    )
    use_custom_range: BoolProperty(name="Custom Range", default=False)
    frame_start: IntProperty(name="Start", default=1, min=0)
    frame_end:   IntProperty(name="End",   default=250, min=1)
    action_filter: StringProperty(
        name="Action Filter",
        description="Only render actions whose name contains this string (blank = all)",
        default="",
    )


class BBUtilsShapeProperties(PropertyGroup):
    shape_preset: EnumProperty(
        name="Shape",
        description="Bone custom shape to assign in bulk",
        items=[(code, label, desc) for code, label, desc in _SHAPE_PRESETS],
        default='OCTAHEDRAL',
    )
    custom_shape_object: PointerProperty(
        name="Object",
        description="Mesh/curve/surface/text object to use as the custom shape",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type in {'MESH', 'CURVE', 'SURFACE', 'FONT'},
    )


class BBUtilsControllerProperties(PropertyGroup):
    suffix: StringProperty(
        name="Suffix",
        description="Suffix added to controller bones",
        default="Ctrl",
    )
    hide_original: BoolProperty(
        name="Hide Original Bones",
        description="Hide original bones after creating controllers",
        default=True,
    )
    remove_all_constraints: BoolProperty(
        name="Remove All Constraints",
        description="Remove ALL constraints from original bones before adding Copy Transforms",
        default=True,
    )
    color_controllers: BoolProperty(
        name="Color Controllers",
        description="Apply a distinct color to controller bones",
        default=True,
    )
    controller_color: FloatVectorProperty(
        name="Controller Color",
        subtype='COLOR',
        default=(0.2, 0.6, 1.0, 1.0),
        min=0.0, max=1.0,
        size=4,
    )
    create_collection: BoolProperty(
        name="Create Collection",
        description="Create a collection for controller bones",
        default=False,
    )
    collection_name: StringProperty(
        name="Collection Name",
        description="Name of the collection for controller bones",
        default="Controllers",
    )


def _ezphyzi_on_resample(self, context):
    """Release frame / bone changed -> the start state must be re-sampled."""
    if _ez_state["handle"] is not None:
        _ez_refresh(context, resample=True)


def _ezphyzi_on_sim(self, context):
    """A physics knob changed -> only the cheap simulation is re-run."""
    if _ez_state["handle"] is not None:
        _ez_refresh(context, resample=False)


class BBUtilsEzPhyziProperties(PropertyGroup):
    armature: PointerProperty(
        name="Armature", type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'ARMATURE',
        update=_ezphyzi_on_resample,
    )
    bone_name: StringProperty(
        name="Bone", description="Bone to throw (magazine, shell, grenade pin...)",
        update=_ezphyzi_on_resample,
    )
    release_frame: IntProperty(
        name="Release Frame",
        description="Frame on which the object leaves the hand",
        default=1, update=_ezphyzi_on_resample,
    )
    duration: IntProperty(
        name="Duration", description="How many frames to simulate after release",
        default=30, min=1, max=1000, update=_ezphyzi_on_sim,
    )
    gravity_scale: FloatProperty(
        name="Gravity", description="Multiplier of the scene gravity",
        default=1.0, min=0.0, soft_max=10.0, update=_ezphyzi_on_sim,
    )
    inherit_velocity: FloatProperty(
        name="Inherit Velocity",
        description="How much of the animated hand velocity the object keeps",
        default=1.0, min=0.0, soft_max=3.0, update=_ezphyzi_on_sim,
    )
    inherit_spin: FloatProperty(
        name="Inherit Spin",
        description="How much of the animated rotation speed the object keeps",
        default=1.0, min=0.0, soft_max=3.0, update=_ezphyzi_on_sim,
    )
    extra_impulse: FloatVectorProperty(
        name="Extra Push", subtype='VELOCITY', size=3,
        description="Extra velocity added at release, in the bone's LOCAL axes (m/s)",
        default=(0.0, 0.0, 0.0), update=_ezphyzi_on_sim,
    )
    extra_spin: FloatVectorProperty(
        name="Extra Spin", size=3,
        description="Extra angular velocity at release, in the bone's LOCAL axes (rad/s)",
        default=(0.0, 0.0, 0.0), update=_ezphyzi_on_sim,
    )
    linear_drag: FloatProperty(
        name="Air Drag", description="Linear velocity damping per second",
        default=0.0, min=0.0, soft_max=5.0, update=_ezphyzi_on_sim,
    )
    angular_damping: FloatProperty(
        name="Spin Damping", description="Angular velocity damping per second",
        default=0.0, min=0.0, soft_max=5.0, update=_ezphyzi_on_sim,
    )
    use_floor: BoolProperty(
        name="Floor", description="Collide the bone origin with a horizontal floor",
        default=False, update=_ezphyzi_on_sim,
    )
    floor_height: FloatProperty(
        name="Floor Z", subtype='DISTANCE', default=0.0, update=_ezphyzi_on_sim,
    )
    bounce: FloatProperty(
        name="Bounce", description="Restitution: 0 = dead stop, 1 = perfect bounce",
        default=0.3, min=0.0, max=1.0, update=_ezphyzi_on_sim,
    )
    friction: FloatProperty(
        name="Friction", description="How much sliding speed is lost on contact",
        default=0.5, min=0.0, max=1.0, update=_ezphyzi_on_sim,
    )
    show_in_front: BoolProperty(
        name="In Front",
        description="Draw the path, ghosts and pivot on top of everything "
                    "instead of letting scene geometry hide them",
        default=False, update=_ezphyzi_on_sim,
    )
    ghost_step: IntProperty(
        name="Ghost Every", description="Draw a ghost of the mesh every N frames",
        default=5, min=1, max=100, update=_ezphyzi_on_sim,
    )
    key_constraints_off: BoolProperty(
        name="Disable Bone Constraints",
        description="Key the bone's constraint influences to 0 from the release frame",
        default=True,
    )
    pivot_offset: FloatVectorProperty(
        name="Pivot", subtype='TRANSLATION', size=3,
        description="Point the object spins around, offset from the bone head "
                    "in the bone's LOCAL space (shown as the yellow dot)",
        default=(0.0, 0.0, 0.0), update=_ezphyzi_on_sim,
    )
    hide_real_mesh: BoolProperty(
        name="Hide Real Mesh In Flight",
        description="While previewing, hide the real (unbaked) mesh after the "
                    "release frame so only the simulated one is visible",
        default=True, update=_ezphyzi_on_sim,
    )
    key_type: EnumProperty(
        name="Key Type", description="Keyframe type given to the baked keys",
        items=[
            ('JITTER',      "Jitter",      "Small green keys - typical for baked / simulated motion"),
            ('GENERATED',   "Generated",   "Keys marked as generated by a tool"),
            ('BREAKDOWN',   "Breakdown",   "Small cyan keys"),
            ('MOVING_HOLD', "Moving Hold", "Dark grey keys"),
            ('EXTREME',     "Extreme",     "Big pink keys"),
            ('KEYFRAME',    "Keyframe",    "Normal keys"),
        ],
        default='JITTER',
    )


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 1 — ARMATURE CONSTRAINT TOOLS
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_OT_apply_child_of_constraints(Operator):
    """Apply Child Of constraints from source armature to matching bones in
    target armature. Select two armatures — the active object is the target."""
    bl_idname = "bbutils.apply_child_of_constraints"
    bl_label = "Apply Child Of Constraints"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        arms = [o for o in context.selected_objects if o.type == 'ARMATURE']
        return len(arms) == 2

    def execute(self, context):
        arms = [o for o in context.selected_objects if o.type == 'ARMATURE']
        if len(arms) != 2:
            self.report({'ERROR'}, "Select exactly 2 armatures")
            return {'CANCELLED'}

        target = context.active_object if context.active_object in arms else arms[1]
        source = arms[0] if arms[1] == target else arms[1]

        original_active = context.view_layer.objects.active
        original_mode   = context.object.mode if context.object else 'OBJECT'

        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        constraints_added = 0
        skipped_hidden    = 0
        skipped_nomatch   = 0

        for src_bone in source.pose.bones:
            if src_bone.bone.hide:
                skipped_hidden += 1
                continue

            tgt_name = src_bone.name
            if tgt_name.endswith("Dupe"):
                tgt_name = tgt_name[:-4]

            tgt_bone = target.pose.bones.get(tgt_name)
            if not tgt_bone or tgt_bone.bone.hide:
                skipped_nomatch += 1
                continue

            con = src_bone.constraints.new('CHILD_OF')
            con.target    = target
            con.subtarget = tgt_name

            context.view_layer.objects.active = source
            bpy.ops.object.mode_set(mode='POSE')
            source.data.bones.active = src_bone.bone
            with context.temp_override(
                active_object=source,
                object=source,
                constraint=con,
            ):
                bpy.ops.constraint.childof_set_inverse(
                    constraint=con.name,
                    owner='BONE',
                )
            bpy.ops.object.mode_set(mode='OBJECT')

            constraints_added += 1

        context.view_layer.objects.active = original_active
        if original_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=original_mode)
            except Exception:
                pass

        self.report(
            {'INFO'},
            f"Added {constraints_added} Child Of constraints "
            f"({skipped_hidden} hidden skipped, {skipped_nomatch} unmatched): "
            f"{source.name} \u2192 {target.name}"
        )
        return {'FINISHED'}


class BBUTILS_OT_connect_rigs(Operator):
    """Connect two selected rigs: adds Copy Transforms constraints on every
    bone of the first-selected rig, targeting the same-named bone in the
    last-selected (active) rig."""
    bl_idname = "bbutils.connect_rigs"
    bl_label = "Connect Rigs"
    bl_options = {'REGISTER', 'UNDO'}

    CONSTRAINT_NAME = "BBUtils Connect"

    @classmethod
    def poll(cls, context):
        arms = [o for o in context.selected_objects if o.type == 'ARMATURE']
        return len(arms) == 2 and context.active_object in arms

    def execute(self, context):
        arms = [o for o in context.selected_objects if o.type == 'ARMATURE']
        if len(arms) != 2 or context.active_object not in arms:
            self.report({'ERROR'},
                        "Select exactly 2 armatures (active = the one to connect to)")
            return {'CANCELLED'}

        target = context.active_object
        source = arms[0] if arms[1] == target else arms[1]

        added = 0
        updated = 0
        unmatched = 0

        for src_bone in source.pose.bones:
            tgt_bone = target.pose.bones.get(src_bone.name)
            if not tgt_bone:
                unmatched += 1
                continue

            con = src_bone.constraints.get(self.CONSTRAINT_NAME)
            if con and con.type == 'COPY_TRANSFORMS':
                updated += 1
            else:
                con = src_bone.constraints.new('COPY_TRANSFORMS')
                con.name = self.CONSTRAINT_NAME
                added += 1

            con.target = target
            con.subtarget = tgt_bone.name

        self.report(
            {'INFO'},
            f"Connected {source.name} \u2192 {target.name}: "
            f"{added} added, {updated} updated, {unmatched} unmatched"
        )
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 2 — CONTROLLER BONES TOOLS
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_OT_create_controller_bones(Operator):
    """Create controller bones that drive selected bones via Copy Transforms."""
    bl_idname = "bbutils.create_controller_bones"
    bl_label = "Create Controller Bones"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                context.object.type == 'ARMATURE' and
                context.object.mode in {'POSE', 'EDIT'})

    def invoke(self, context, event):
        obj = context.object
        if obj.mode == 'POSE':
            selected_count = len(context.selected_pose_bones or [])
        else:
            selected_count = sum(1 for b in obj.data.edit_bones if b.select)

        if selected_count == 0:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        props = context.scene.bbutils_controller_props

        box = layout.box()
        box.label(text="Selected Bones", icon='BONE_DATA')
        obj = context.object
        if obj.mode == 'POSE':
            selected_bones = context.selected_pose_bones or []
            box.label(text=f"  {len(selected_bones)} bone(s) selected", icon='CHECKBOX_HLT')
        else:
            selected_bones = [b for b in obj.data.edit_bones if b.select]
            box.label(text=f"  {len(selected_bones)} bone(s) in Edit Mode", icon='CHECKBOX_HLT')

        box = layout.box()
        box.label(text="Naming", icon='SORTALPHA')
        box.prop(props, "suffix", text="Suffix")

        box = layout.box()
        box.label(text="Behavior", icon='TOOL_SETTINGS')
        box.prop(props, "remove_all_constraints")
        box.prop(props, "hide_original")

        box = layout.box()
        box.label(text="Visual", icon='COLOR')
        box.prop(props, "color_controllers")
        if props.color_controllers:
            box.prop(props, "controller_color", text="")

        box = layout.box()
        box.label(text="Organization", icon='OUTLINER')
        box.prop(props, "create_collection")
        if props.create_collection:
            box.prop(props, "collection_name")

    def execute(self, context):
        obj = context.object
        props = context.scene.bbutils_controller_props

        if obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Active object is not an armature")
            return {'CANCELLED'}

        original_mode = obj.mode

        if original_mode == 'POSE':
            selected = {b.name for b in (context.selected_pose_bones or [])}
        else:
            selected = {b.name for b in obj.data.edit_bones if b.select}

        if not selected:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        # Keep armature order so originals and their duplicates line up
        order = {b.name: i for i, b in enumerate(obj.data.bones)}
        selected_bone_names = sorted(selected, key=lambda n: order.get(n, 0))
        original_selection  = _selected_bone_names(obj)

        bpy.ops.object.mode_set(mode='EDIT')
        edit_bones = obj.data.edit_bones

        # Remember where each original sits so duplicates can be matched
        # to their originals by position instead of trusting list order.
        geo = {n: (edit_bones[n].head.copy(), edit_bones[n].tail.copy())
               for n in selected_bone_names if n in edit_bones}
        selected_bone_names = [n for n in selected_bone_names if n in geo]

        bpy.ops.armature.select_all(action='DESELECT')
        for bone_name in selected_bone_names:
            bone = edit_bones[bone_name]
            bone.select = True
            bone.select_head = True
            bone.select_tail = True

        bpy.ops.armature.duplicate()

        remaining = [b for b in edit_bones
                     if b.select and b.name not in geo]
        duplicated_names = []

        for original_name in selected_bone_names:
            if not remaining:
                duplicated_names.append(None)
                continue
            head, tail = geo[original_name]
            duped_bone = min(
                remaining,
                key=lambda d: (d.head - head).length + (d.tail - tail).length)
            remaining.remove(duped_bone)

            new_name = f"{original_name}{props.suffix}"
            counter = 1
            final_name = new_name
            while final_name in edit_bones:
                final_name = f"{new_name}.{counter:03d}"
                counter += 1
            duped_bone.name = final_name
            duped_bone.parent = None
            duplicated_names.append(final_name)

        bpy.ops.object.mode_set(mode='POSE')

        constraints_removed = 0
        if props.remove_all_constraints:
            for bone_name in selected_bone_names:
                bone = obj.pose.bones.get(bone_name)
                if bone and bone.constraints:
                    constraints_removed += len(bone.constraints)
                    for constraint in list(bone.constraints):
                        bone.constraints.remove(constraint)

        success_count = 0
        for original_name, dupe_name in zip(selected_bone_names, duplicated_names):
            if not dupe_name:
                continue
            original_bone   = obj.pose.bones.get(original_name)
            controller_bone = obj.pose.bones.get(dupe_name)
            if original_bone and controller_bone:
                constraint = original_bone.constraints.new('COPY_TRANSFORMS')
                constraint.target = obj
                constraint.subtarget = dupe_name
                success_count += 1

        if props.hide_original:
            for bone_name in selected_bone_names:
                bone = obj.pose.bones.get(bone_name)
                if bone:
                    bone.bone.hide = True

        if props.color_controllers:
            r, g, b, _ = props.controller_color
            for dupe_name in duplicated_names:
                pbone = obj.pose.bones.get(dupe_name) if dupe_name else None
                if pbone:
                    pbone.bone.color.palette = 'CUSTOM'
                    pbone.bone.color.custom.normal = (r, g, b)
                    pbone.bone.color.custom.select = (
                        min(r + 0.2, 1.0), min(g + 0.2, 1.0), min(b + 0.2, 1.0))
                    pbone.bone.color.custom.active = (1.0, 1.0, 1.0)

        if props.create_collection and any(duplicated_names):
            coll_name = props.collection_name or "Controllers"
            bone_coll = next((c for c in obj.data.collections_all
                              if c.name == coll_name), None)
            if bone_coll is None:
                bone_coll = obj.data.collections.new(coll_name)
            for dupe_name in duplicated_names:
                bone = obj.data.bones.get(dupe_name) if dupe_name else None
                if bone:
                    bone_coll.assign(bone)

        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.armature.select_all(action='DESELECT')
        for bone_name in original_selection:
            bone = obj.data.edit_bones.get(bone_name)
            if bone:
                bone.select = True

        bpy.ops.object.mode_set(mode=original_mode)

        self.report({'INFO'},
                    f"Created {success_count} controller bone(s) | "
                    f"Removed {constraints_removed} constraint(s)")

        _redraw(context, {'VIEW_3D'})
        return {'FINISHED'}


class BBUTILS_OT_remove_controller_bones(Operator):
    """Remove controller bones and restore original bones visibility."""
    bl_idname = "bbutils.remove_controller_bones"
    bl_label = "Remove Controller Bones"
    bl_options = {'REGISTER', 'UNDO'}

    controller_suffix: StringProperty(
        name="Controller Suffix",
        description="Suffix used for controller bones",
        default="Ctrl",
    )
    delete_controllers: BoolProperty(
        name="Delete Controller Bones",
        description="Permanently delete controller bones",
        default=True,
    )
    unhide_originals: BoolProperty(
        name="Unhide Original Bones",
        description="Make original bones visible again",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.object and context.object.type == 'ARMATURE')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        obj = context.object
        suffix = self.controller_suffix

        controller_bones = []
        if suffix:
            for bone in obj.pose.bones:
                if (bone.name.endswith(suffix) and len(bone.name) > len(suffix)
                        and bone.name[:-len(suffix)] in obj.pose.bones):
                    controller_bones.append(bone.name)

        box = layout.box()
        box.label(text="Detected Controller Bones", icon='BONE_DATA')
        if controller_bones:
            for name in controller_bones[:10]:
                box.label(text=f"  \u2022 {name}", icon='BONE_DATA')
            if len(controller_bones) > 10:
                box.label(text=f"  ... and {len(controller_bones) - 10} more", icon='DOT')
        else:
            box.label(text="  No controller bones detected", icon='INFO')

        layout.prop(self, "controller_suffix")
        layout.prop(self, "delete_controllers")
        layout.prop(self, "unhide_originals")

    def execute(self, context):
        obj = context.object
        suffix = self.controller_suffix

        if not suffix:
            self.report({'WARNING'}, "Please specify a controller suffix")
            return {'CANCELLED'}

        # Only bones that really are controllers: name ends with the suffix
        # AND the un-suffixed original bone exists.
        pairs = []   # (controller name, original name)
        for bone in obj.pose.bones:
            if bone.name.endswith(suffix) and len(bone.name) > len(suffix):
                original_name = bone.name[:-len(suffix)]
                if original_name in obj.pose.bones:
                    pairs.append((bone.name, original_name))

        if not pairs:
            self.report({'WARNING'}, f"No controller bones ending with '{suffix}' found")
            return {'CANCELLED'}

        controller_names = {c for c, _ in pairs}
        original_mode = obj.mode

        # Remove the Copy Transforms constraints that point at the controllers
        # (they would turn red/invalid once the controllers are gone)
        constraints_removed = 0
        if self.delete_controllers:
            for _, original_name in pairs:
                original = obj.pose.bones.get(original_name)
                if not original:
                    continue
                for con in list(original.constraints):
                    if (getattr(con, "target", None) == obj and
                            getattr(con, "subtarget", "") in controller_names):
                        original.constraints.remove(con)
                        constraints_removed += 1

        if self.unhide_originals:
            for _, original_name in pairs:
                original = obj.pose.bones.get(original_name)
                if original:
                    original.bone.hide = False

        if self.delete_controllers:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.armature.select_all(action='DESELECT')
            for name in controller_names:
                bone = obj.data.edit_bones.get(name)
                if bone:
                    bone.select = True
                    bone.select_head = True
                    bone.select_tail = True
            bpy.ops.armature.delete()
            try:
                bpy.ops.object.mode_set(mode=original_mode)
            except Exception:
                bpy.ops.object.mode_set(mode='OBJECT')

        self.report({'INFO'},
                    f"Removed {len(pairs)} controller bone(s) | "
                    f"Unhid {len(pairs)} original bone(s) | "
                    f"Removed {constraints_removed} constraint(s)")

        _redraw(context, {'VIEW_3D'})
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 2B — BONE DISPLAY TOOLS
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_OT_reset_custom_shape(Operator):
    """Reset the custom shape (and/or its transform) on selected pose bones."""
    bl_idname = "bbutils.reset_custom_shape"
    bl_label = "Reset Custom Shape"
    bl_options = {'REGISTER', 'UNDO'}

    clear_shape_object: BoolProperty(
        name="Clear Shape Object",
        description="Unassign the custom shape object, reverting to the default bone shape",
        default=True,
    )
    reset_transform: BoolProperty(
        name="Reset Shape Transform",
        description="Reset custom shape translation, rotation, scale and wire width to defaults",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                context.object.type == 'ARMATURE' and
                context.object.mode == 'POSE' and
                    bool(context.selected_pose_bones))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        selected = context.selected_pose_bones or []
        layout.label(text=f"Bones selected: {len(selected)}")
        layout.prop(self, "clear_shape_object")
        layout.prop(self, "reset_transform")

    def execute(self, context):
        selected_bones = context.selected_pose_bones
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        if not self.clear_shape_object and not self.reset_transform:
            self.report({'WARNING'}, "Nothing to do — enable at least one option")
            return {'CANCELLED'}

        reset_count = 0
        for pb in selected_bones:
            changed = False

            if self.clear_shape_object and pb.custom_shape:
                pb.custom_shape = None
                changed = True

            if self.reset_transform:
                pb.custom_shape_translation    = (0.0, 0.0, 0.0)
                pb.custom_shape_rotation_euler = (0.0, 0.0, 0.0)
                pb.custom_shape_scale_xyz      = (1.0, 1.0, 1.0)
                if hasattr(pb, "use_custom_shape_bone_size"):
                    pb.use_custom_shape_bone_size = True
                if hasattr(pb, "custom_shape_wire_width"):
                    pb.custom_shape_wire_width = 1.0
                pb.custom_shape_transform = None
                changed = True

            if changed:
                reset_count += 1

        self.report({'INFO'}, f"Reset custom shape on {reset_count} bone(s)")
        _redraw(context, {'VIEW_3D', 'PROPERTIES'})
        return {'FINISHED'}


class BBUTILS_OT_set_custom_shape(Operator):
    """Assign one custom shape (a chosen object, or an auto-generated preset)
    to ALL selected pose bones at once — the built-in Bone properties panel
    only lets you set one bone's shape at a time."""
    bl_idname = "bbutils.set_custom_shape"
    bl_label = "Set Custom Shape (Bulk)"
    bl_options = {'REGISTER', 'UNDO'}

    # NOTE: the shape object itself lives in Scene.bbutils_shape_props
    # (context.scene.bbutils_shape_props), not as an operator property —
    # ID-pointer properties (PointerProperty(type=Object)) are unreliable
    # on Operators and can raise AttributeError on execute/redo.
    use_bone_size: BoolProperty(
        name="Scale to Bone Length",
        description="Enable 'Scale to Bone Length' so the shape scales with each bone",
        default=True,
    )
    shape_scale: FloatVectorProperty(
        name="Shape Scale",
        description="Extra XYZ scale applied to the custom shape on every selected bone",
        default=(1.0, 1.0, 1.0),
        min=0.0001,
        size=3,
    )
    overwrite_existing: BoolProperty(
        name="Overwrite Existing Shapes",
        description="If off, bones that already have a custom shape assigned are left untouched",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                context.object.type == 'ARMATURE' and
                context.object.mode == 'POSE' and
                    bool(context.selected_pose_bones))

    def invoke(self, context, event):
        shape_props = context.scene.bbutils_shape_props
        if shape_props.shape_preset == 'CUSTOM' and not shape_props.custom_shape_object:
            self.report({'ERROR'}, "Pick a custom shape object in the panel first")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        shape_props = context.scene.bbutils_shape_props
        selected = context.selected_pose_bones or []
        layout.label(text=f"Bones selected: {len(selected)}")
        if shape_props.shape_preset == 'CUSTOM':
            layout.label(text=f"Shape: {shape_props.custom_shape_object.name}"
                         if shape_props.custom_shape_object else "Shape: (none picked)")
        else:
            label = next((lbl for code, lbl, _ in _SHAPE_PRESETS
                          if code == shape_props.shape_preset), shape_props.shape_preset)
            layout.label(text=f"Shape: {label} (auto-generated)")
        layout.prop(self, "use_bone_size")
        layout.prop(self, "shape_scale")
        layout.prop(self, "overwrite_existing")

    def execute(self, context):
        selected_bones = context.selected_pose_bones
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        shape_props = context.scene.bbutils_shape_props
        if shape_props.shape_preset == 'CUSTOM':
            shape_obj = shape_props.custom_shape_object
            if not shape_obj:
                self.report({'ERROR'}, "No shape object chosen")
                return {'CANCELLED'}
        else:
            shape_obj = _get_or_create_widget(context, shape_props.shape_preset)

        applied_count = 0
        skipped_count = 0
        for pb in selected_bones:
            if not self.overwrite_existing and pb.custom_shape:
                skipped_count += 1
                continue

            pb.custom_shape = shape_obj
            pb.custom_shape_scale_xyz = tuple(self.shape_scale)
            if hasattr(pb, "use_custom_shape_bone_size"):
                pb.use_custom_shape_bone_size = self.use_bone_size
            applied_count += 1

        self.report(
            {'INFO'},
            f"Assigned '{shape_obj.name}' to {applied_count} bone(s)"
            + (f" ({skipped_count} skipped — already had a shape)" if skipped_count else "")
        )
        _redraw(context, {'VIEW_3D', 'PROPERTIES'})
        return {'FINISHED'}


class BBUTILS_OT_unlock_channels(Operator):
    """Clear the Location/Rotation/Scale transform locks on selected pose
    bones (the little padlock icons in the N-panel Transform section)."""
    bl_idname = "bbutils.unlock_channels"
    bl_label = "Unlock All Channels"
    bl_options = {'REGISTER', 'UNDO'}

    unlock_location: BoolProperty(name="Location", default=True)
    unlock_rotation: BoolProperty(name="Rotation", default=True)
    unlock_scale:    BoolProperty(name="Scale",    default=True)

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                context.object.type == 'ARMATURE' and
                context.object.mode == 'POSE' and
                    bool(context.selected_pose_bones))

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        selected = context.selected_pose_bones or []
        layout.label(text=f"Bones selected: {len(selected)}")
        layout.prop(self, "unlock_location")
        layout.prop(self, "unlock_rotation")
        layout.prop(self, "unlock_scale")

    def execute(self, context):
        selected_bones = context.selected_pose_bones
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        if not (self.unlock_location or self.unlock_rotation or self.unlock_scale):
            self.report({'WARNING'}, "Nothing to do — enable at least one channel")
            return {'CANCELLED'}

        unlocked_count = 0
        for pb in selected_bones:
            changed = False

            if self.unlock_location:
                for i in range(3):
                    if pb.lock_location[i]:
                        pb.lock_location[i] = False
                        changed = True

            if self.unlock_rotation:
                for i in range(3):
                    if pb.lock_rotation[i]:
                        pb.lock_rotation[i] = False
                        changed = True
                if pb.lock_rotation_w:
                    pb.lock_rotation_w = False
                    changed = True

            if self.unlock_scale:
                for i in range(3):
                    if pb.lock_scale[i]:
                        pb.lock_scale[i] = False
                        changed = True

            if changed:
                unlocked_count += 1

        if unlocked_count == 0:
            self.report({'INFO'}, "No locked channels found on selected bone(s)")
        else:
            self.report({'INFO'}, f"Unlocked channels on {unlocked_count} bone(s)")
        _redraw(context, {'VIEW_3D', 'PROPERTIES'})
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 3 — ANIMATION TOOLS
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_OT_wipe_animation_data(Operator):
    """Remove all F-Curves for selected pose bones across actions in the file"""
    bl_idname = "bbutils.wipe_animation_data"
    bl_label = "Wipe Animation Data"
    bl_options = {'REGISTER', 'UNDO'}

    scope: EnumProperty(
        name="Scope",
        items=[
            ('ALL',    "All Actions",   "Wipe across every action in the file"),
            ('ACTIVE', "Active Action", "Wipe only the currently active action"),
        ],
        default='ALL',
    )

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                context.object.type == 'ARMATURE' and
                context.object.mode == 'POSE')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        layout.label(text="This permanently deletes F-Curves!", icon='ERROR')
        layout.prop(self, "scope", expand=True)
        selected = context.selected_pose_bones or []
        layout.label(text=f"Bones selected: {len(selected)}")

    def execute(self, context):
        obj            = context.object
        selected_bones = {b.name for b in (context.selected_pose_bones or [])}
        if not selected_bones:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        if self.scope == 'ACTIVE':
            if not obj.animation_data or not obj.animation_data.action:
                self.report({'WARNING'}, "No active action")
                return {'CANCELLED'}
            actions, skipped = [obj.animation_data.action], 0
        else:
            actions, skipped = _rig_actions(obj)

        wiped_count = 0
        for action in actions:
            to_remove = []
            for fc in _get_action_fcurves(action, obj):
                if fc.data_path.startswith("pose.bones["):
                    s = fc.data_path.find('"') + 1
                    e = fc.data_path.find('"', s)
                    if s > 0 and e > s and fc.data_path[s:e] in selected_bones:
                        to_remove.append(fc)

            for fc in to_remove:
                try:
                    if _remove_fcurve(action, fc):
                        wiped_count += 1
                except Exception:
                    pass
            if hasattr(action, "update_tag"):
                action.update_tag()

        msg = (f"Wiped {wiped_count} F-Curves for {len(selected_bones)} bone(s) "
               f"across {len(actions)} action(s)")
        if skipped:
            msg += f" ({skipped} skipped: no slot for this rig)"
        self.report({'INFO'}, msg)
        _redraw(context, {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'})
        return {'FINISHED'}


class BBUTILS_OT_key_constraint_influences(Operator):
    """Key the influence of every constraint on the selected bones at the
    current frame - in the active action and, optionally, in every other
    action of this rig."""
    bl_idname = "bbutils.key_constraint_influences"
    bl_label = "Key Constraint Influences"
    bl_options = {'REGISTER', 'UNDO'}

    all_actions: BoolProperty(
        name="All Actions",
        description="Also key the current frame in every other action of this rig "
                    "(otherwise only the active action is keyed)",
        default=True,
    )
    only_nonzero: BoolProperty(
        name="Skip Zero Influences",
        description="Skip constraints whose current influence is 0",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                    context.object.type == 'ARMATURE' and
                    context.object.mode == 'POSE')

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "all_actions")
        layout.prop(self, "only_nonzero")

    def execute(self, context):
        obj      = context.object
        frame    = float(context.scene.frame_current)
        selected = list(context.selected_pose_bones or [])

        if not selected:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        # Read the influences ONCE, exactly as shown in the properties panel.
        # These are the values that get keyed into every action.
        entries = []   # (bone name, data path, value)
        for bone in selected:
            for con in bone.constraints:
                value = con.influence
                if self.only_nonzero and value == 0.0:
                    continue
                entries.append((bone.name, con.path_from_id("influence"), value))

        if not entries:
            self.report({'WARNING'}, "No constraints found on the selected bones")
            return {'CANCELLED'}

        anim = _ensure_animation_data(obj)
        if anim.action is None:
            _assign_action(obj, bpy.data.actions.new(name=f"{obj.name}Action"))
        active_action = anim.action

        if self.all_actions:
            actions, skipped = _rig_actions(obj)
        else:
            actions, skipped = [active_action], 0
        if active_action not in actions:
            actions.insert(0, active_action)

        keyed_count   = 0
        keyed_actions = 0
        failed        = []

        for action in actions:
            try:
                for bone_name, data_path, value in entries:
                    fc = _ensure_fcurve(action, obj, data_path, 0, bone_name)
                    _set_key(fc, frame, value)
                    keyed_count += 1
                keyed_actions += 1
            except Exception as ex:
                failed.append(f"{action.name}: {ex}")

        obj.update_tag()
        context.view_layer.update()

        msg = (f"Keyed {keyed_count} constraint influence(s) at frame "
               f"{int(frame)} across {keyed_actions} action(s)")
        if skipped:
            msg += f" ({skipped} skipped: no slot for this rig)"
        self.report({'INFO'}, msg)
        if failed:
            self.report({'WARNING'}, f"{len(failed)} action(s) failed - " + failed[0])
        _redraw(context, {'DOPESHEET_EDITOR', 'GRAPH_EDITOR', 'VIEW_3D'})
        return {'FINISHED'}


class BBUTILS_OT_key_matching_channels(Operator):
    """At the current frame, insert keyframes in all other actions for every
    channel that exists in the active action."""
    bl_idname = "bbutils.key_matching_channels"
    bl_label = "Key Matching Channels"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                    context.object.animation_data and
                        context.object.animation_data.action)

    def execute(self, context):
        obj           = context.object
        frame         = float(context.scene.frame_current)
        active_action = obj.animation_data.action

        actions, skipped = _rig_actions(obj)
        targets = [a for a in actions if a != active_action]
        if not targets:
            self.report({'WARNING'}, "No other actions found for this rig")
            return {'CANCELLED'}

        depsgraph = context.evaluated_depsgraph_get()
        eval_obj  = obj.evaluated_get(depsgraph)

        selected_names = [b.name for b in (context.selected_pose_bones or [])]

        source_curves = []
        for fc in _get_action_fcurves(active_action, obj):
            if selected_names and not any(f'pose.bones["{n}"]' in fc.data_path
                                          for n in selected_names):
                continue
            source_curves.append(fc)

        if not source_curves:
            self.report({'WARNING'}, "No matching channels in the active action")
            return {'CANCELLED'}

        keyed_count = 0
        failed = []

        for action in targets:
            try:
                for fc in source_curves:
                    group = fc.group.name if fc.group else ""
                    target_fc = _ensure_fcurve(action, obj, fc.data_path,
                                               fc.array_index, group)
                    if any(abs(kp.co[0] - frame) < 0.001
                           for kp in target_fc.keyframe_points):
                        continue

                    v = 0.0
                    for src in (obj, eval_obj):
                        try:
                            value = src.path_resolve(fc.data_path)
                            v = (value[fc.array_index]
                                 if hasattr(value, '__len__') else float(value))
                            break
                        except Exception:
                            continue

                    _set_key(target_fc, frame, v)
                    keyed_count += 1
            except Exception as ex:
                failed.append(f"{action.name}: {ex}")

        if keyed_count == 0 and not failed:
            self.report({'INFO'}, "No new keyframes needed - all channels already keyed")
        else:
            msg = f"Added {keyed_count} keyframes across {len(targets)} action(s)"
            if skipped:
                msg += f" ({skipped} skipped: no slot for this rig)"
            self.report({'INFO'}, msg)
        if failed:
            self.report({'WARNING'}, f"{len(failed)} action(s) failed - " + failed[0])

        _redraw(context, {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'})
        return {'FINISHED'}


class BBUTILS_OT_stash_all_actions(Operator):
    """Push all actions (including active) into NLA tracks starting at frame 0"""
    bl_idname = "bbutils.stash_all_actions"
    bl_label = "Stash All Actions"
    bl_options = {'REGISTER', 'UNDO'}

    fallback_to_active: BoolProperty(
        name="Fallback to Active Object",
        description="Stash ownerless actions on the active object instead of skipping them",
        default=True,
    )
    include_active: BoolProperty(
        name="Include Active Action",
        description="Also stash the currently active action",
        default=True,
    )
    start_frame: IntProperty(
        name="Start Frame",
        description="Frame where NLA strip starts",
        default=0,
        min=0,
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None and bool(bpy.data.actions)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "include_active")
        layout.prop(self, "fallback_to_active")
        layout.prop(self, "start_frame")
        layout.label(text=f"Actions to stash: {len(bpy.data.actions)}", icon='ACTION')

    def execute(self, context):
        active_obj = context.object
        owner_map  = _action_owner_map()
        stashed    = 0
        skipped    = 0

        for action in bpy.data.actions:
            owner = owner_map.get(action)

            is_active = False
            if owner and owner.animation_data and owner.animation_data.action == action:
                is_active = True
                if not self.include_active:
                    skipped += 1
                    continue

            if owner is None:
                if self.fallback_to_active:
                    owner = active_obj
                else:
                    skipped += 1
                    continue

            anim = _ensure_animation_data(owner)

            if is_active and anim.action == action:
                anim.action = None

            existing_names = {s.name for t in anim.nla_tracks for s in t.strips}
            if action.name in existing_names:
                skipped += 1
                continue

            track = anim.nla_tracks.new()
            track.name = action.name

            try:
                strip = track.strips.new(name=action.name, start=self.start_frame, action=action)
            except RuntimeError:
                anim.nla_tracks.remove(track)
                skipped += 1
                continue

            # Layered actions: the strip needs a slot or it animates nothing
            if hasattr(strip, "action_slot") and strip.action_slot is None:
                slot = _slot_for_obj(action, owner)
                if slot is not None:
                    strip.action_slot = slot

            stashed += 1

        self.report({'INFO'},
                    f"Stashed {stashed} action(s) to NLA ({skipped} skipped)")
        _redraw(context, {'DOPESHEET_EDITOR', 'NLA_EDITOR'})
        return {'FINISHED'}


class BBUTILS_OT_delete_all_nla_tracks(Operator):
    """Delete every NLA track from every object in the scene."""
    bl_idname = "bbutils.delete_all_nla_tracks"
    bl_label = "Delete All NLA Tracks"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(
            obj.animation_data and obj.animation_data.nla_tracks
            for obj in bpy.data.objects
        )

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        deleted = 0
        for obj in bpy.data.objects:
            if obj.animation_data and obj.animation_data.nla_tracks:
                while obj.animation_data.nla_tracks:
                    obj.animation_data.nla_tracks.remove(
                        obj.animation_data.nla_tracks[0])
                    deleted += 1
        self.report({'INFO'}, f"Deleted {deleted} NLA track(s) from all objects")
        _redraw(context, {'DOPESHEET_EDITOR', 'NLA_EDITOR'})
        return {'FINISHED'}


class BBUTILS_OT_bake_between_keyframes(Operator):
    """Bake selected bones between the first and last selected keyframes."""
    bl_idname = "bbutils.bake_between_keyframes"
    bl_label = "Bake Between Keyframes"
    bl_options = {'REGISTER', 'UNDO'}

    frame_step: IntProperty(
        name="Frame Step",
        description="Step between baked frames (1 = every frame)",
        default=1, min=1, max=10,
    )
    keyframe_type: EnumProperty(
        name="Handle Type",
        items=[
            ('VECTOR',       "Vector",       "Sharp, straight handles between baked frames"),
            ('AUTO_CLAMPED', "Auto Clamped", "Smooth, overshoot-free handles"),
            ('AUTO',         "Auto",         "Smooth handles (may overshoot)"),
        ],
        default='VECTOR',
    )

    @classmethod
    def poll(cls, context):
        return bool(context.object and
                context.object.type == 'ARMATURE' and
                context.object.mode == 'POSE' and
                context.object.animation_data and
                    context.object.animation_data.action)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "frame_step")
        layout.prop(self, "keyframe_type")

    @staticmethod
    def _remove_keyframe_at_frame(fcurve, frame):
        indices = [i for i, kp in enumerate(fcurve.keyframe_points)
                   if abs(kp.co[0] - frame) < 0.001]
        for i in sorted(indices, reverse=True):
            fcurve.keyframe_points.remove(fcurve.keyframe_points[i])

    def execute(self, context):
        obj            = context.object
        action         = obj.animation_data.action
        selected_bones = context.selected_pose_bones

        if not selected_bones:
            self.report({'WARNING'}, "No bones selected")
            return {'CANCELLED'}

        selected_frames = sorted({
            kp.co[0]
            for fc in _get_action_fcurves(action, obj)
            for kp in fc.keyframe_points
            if kp.select_control_point
        })

        if len(selected_frames) < 2:
            self.report({'WARNING'}, "Select at least 2 keyframes (first and last)")
            return {'CANCELLED'}

        start_frame = int(selected_frames[0])
        end_frame   = int(selected_frames[-1])

        if start_frame >= end_frame:
            self.report({'WARNING'}, "Start frame must be before end frame")
            return {'CANCELLED'}

        constraint_influences = {}
        for bone in selected_bones:
            for con in obj.pose.bones[bone.name].constraints:
                if not hasattr(con, 'influence'):
                    continue
                dp = (f'pose.bones["{bone.name}"]'
                      f'.constraints["{con.name}"].influence')
                
                fc = None
                for fcurve in _get_action_fcurves(action, obj):
                    if fcurve.data_path == dp and fcurve.array_index == 0:
                        fc = fcurve
                        break
                if not fc:
                    continue
                
                active_nonzero = [
                    (kp.co[0], kp.co[1])
                    for kp in fc.keyframe_points
                    if start_frame <= kp.co[0] <= end_frame and kp.co[1] != 0.0
                ]
                if active_nonzero:
                    constraint_influences[dp] = [
                        (kp.co[0], kp.co[1]) for kp in fc.keyframe_points
                    ]

        active_channels = {}
        for bone in selected_bones:
            bn = bone.name
            active_channels[bn] = {
                'location':            [False] * 3,
                'rotation_quaternion': [False] * 4,
                'rotation_euler':      [False] * 3,
                'scale':               [False] * 3,
            }
            for fc in _get_action_fcurves(action, obj):
                if f'pose.bones["{bn}"]' not in fc.data_path:
                    continue
                for kp in fc.keyframe_points:
                    if start_frame <= kp.co[0] <= end_frame:
                        for ch, n in [
                            ('location', 3), ('rotation_quaternion', 4),
                            ('rotation_euler', 3), ('scale', 3),
                        ]:
                            if f'.{ch}' in fc.data_path and fc.array_index < n:
                                active_channels[bn][ch][fc.array_index] = True
                        break

        original_frame = context.scene.frame_current
        try:
            sampled = {}
            for frame in range(start_frame, end_frame + 1, self.frame_step):
                context.scene.frame_set(frame)
                depsgraph = context.evaluated_depsgraph_get()
                eval_obj  = obj.evaluated_get(depsgraph)
                sampled[frame] = {
                    b.name: eval_obj.pose.bones[b.name].matrix.copy()
                    for b in selected_bones
                }

            for frame, frame_data in sampled.items():
                context.scene.frame_set(frame)
                for bone in selected_bones:
                    pb = obj.pose.bones[bone.name]
                    pb.matrix = frame_data[bone.name]
                    ac = active_channels[bone.name]

                    if any(ac['location']):
                        pb.keyframe_insert("location", frame=frame)
                    if pb.rotation_mode == 'QUATERNION' and any(ac['rotation_quaternion']):
                        pb.keyframe_insert("rotation_quaternion", frame=frame)
                    elif (pb.rotation_mode in {'XYZ', 'XZY', 'YXZ', 'YZX', 'ZXY', 'ZYX'}
                          and any(ac['rotation_euler'])):
                        pb.keyframe_insert("rotation_euler", frame=frame)
                    if any(ac['scale']):
                        pb.keyframe_insert("scale", frame=frame)

            for fc in _get_action_fcurves(action, obj):
                for kp in fc.keyframe_points:
                    if start_frame <= kp.co[0] <= end_frame:
                        kp.handle_left_type  = self.keyframe_type
                        kp.handle_right_type = self.keyframe_type

            for dp, all_keyframes in constraint_influences.items():
                fc = _ensure_fcurve(action, obj, dp, 0)

                for f in range(start_frame, end_frame + 1):
                    self._remove_keyframe_at_frame(fc, f)

                fc.keyframe_points.insert(start_frame, 0.0)
                fc.keyframe_points.insert(end_frame,   0.0)

                for f, v in all_keyframes:
                    if f < start_frame or f > end_frame:
                        if not any(abs(kp.co[0] - f) < 0.001
                                   for kp in fc.keyframe_points):
                            fc.keyframe_points.insert(f, v)

            for fc in _get_action_fcurves(action, obj):
                if hasattr(fc, "update"):
                    fc.update()

            self.report({'INFO'},
                        f"Baked {len(selected_bones)} bone(s), "
                        f"frames {start_frame}\u2013{end_frame}")

        finally:
            context.scene.frame_set(original_frame)

        _redraw(context, {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'})
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 3b — EZPHYZI (ballistic sim + viewport overlay + bake)
# ─────────────────────────────────────────────────────────────────────────────
#
#  Replaces the "duplicate mesh -> rigid body -> bake -> child of -> bake"
#  workflow. The throw is a pure ballistic flight, so it is integrated here in
#  Python: start velocity / spin come from the bone's own animation at the
#  release frame, then gravity + damping are applied each sub-step. Because it
#  is cheap, the overlay re-simulates live while you drag the sliders.

_EZ_SUBSTEPS = 4
_EZ_MAX_GHOST_EDGES = 8000
_EZ_MAX_SOLID_TRIS = 6000
_EZ_COL_PATH   = (0.25, 1.0, 0.35, 0.95)
_EZ_COL_GHOST  = (0.25, 1.0, 0.35, 0.30)
_EZ_COL_NOW    = (1.0, 1.0, 1.0, 0.55)
_EZ_COL_TICK   = (1.0, 0.6, 0.1, 0.9)
_EZ_COL_SOLID  = (0.62, 0.64, 0.68)
_EZ_COL_PIVOT  = (1.0, 0.85, 0.1, 1.0)
_EZ_LIGHT_DIR  = Vector((0.35, -0.5, 0.8)).normalized()

# bound_box corner order -> 12 edges / 12 triangles of the box
_EZ_BOX_EDGES = ((0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6),
                 (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7))
_EZ_BOX_TRIS = [t for a, b, c, d in ((0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
                                     (3, 2, 6, 7), (0, 3, 7, 4), (1, 5, 6, 2))
                for t in ((a, b, c), (a, c, d))]

_ez_state = {
    "handle": None,     # SpaceView3D draw handler
    "init": None,       # sampled start state (two bone matrices + ghost mesh)
    "frames": None,     # [(pivot_pos, rot), ...] one per frame, 0 = release
    "offset": Vector(), # pivot offset the frames were simulated with
    "batches": None,    # cached static GPU batches
    "ghosts": {},       # frame index -> wire ghost batch
    "solids": {},       # frame index -> shaded solid ghost batch
    "hidden": [],       # real meshes hidden by the preview
    "launch": None,     # frame index of the one-shot launch, None = idle
    "sampling": False,  # suspend auto-hide while evaluating
    "hide_pending": False,
}


def _ez_valid(p):
    return bool(p.armature and p.armature.type == 'ARMATURE'
                and p.bone_name in p.armature.pose.bones)


def _ez_fps(scene):
    return scene.render.fps / (scene.render.fps_base or 1.0)


def _ez_bound_objects(arm, bone_name):
    """Meshes that move rigidly with the bone, at any depth:
    bone -> (empty ->)* mesh via bone parenting, or via a Child Of /
    Copy Transforms constraint that targets the bone."""
    roots = []
    for o in bpy.context.scene.objects:
        if o.parent == arm and o.parent_type == 'BONE' and o.parent_bone == bone_name:
            roots.append(o)
        elif any(c.type in {'CHILD_OF', 'COPY_TRANSFORMS'} and not c.mute
                 and c.influence > 0.0 and c.target == arm
                 and getattr(c, "subtarget", "") == bone_name
                 for c in o.constraints):
            roots.append(o)
    meshes, seen, stack = [], set(), roots
    while stack:
        o = stack.pop()
        if o.name in seen:
            continue
        seen.add(o.name)
        if o.type == 'MESH':
            meshes.append(o)
        stack.extend(o.children)
    return meshes


def _ez_skinned_objects(arm, bone_name, exclude):
    """Meshes deformed by the armature that have a vertex group for the bone
    (only the vertices weighted to it are used for the ghost)."""
    out = []
    for o in bpy.context.scene.objects:
        if (o.type == 'MESH' and o not in exclude and bone_name in o.vertex_groups
                and any(m.type == 'ARMATURE' and m.object == arm for m in o.modifiers)):
            out.append(o)
    return out


def _ez_add_geometry(acc, mesh, to_bone, keep=None):
    """Append `mesh` (optionally only vertex indices in `keep`) to the
    accumulators: wire (verts, edges) and solid (sverts, tris)."""
    verts, edges, sverts, tris = acc
    if keep is None:
        idx_map = {i: i for i in range(len(mesh.vertices))}
    else:
        idx_map = {i: n for n, i in enumerate(sorted(keep))}
    pts = [None] * len(idx_map)
    for i, n in idx_map.items():
        pts[n] = to_bone @ mesh.vertices[i].co
    if not pts:
        return

    lo = Vector(tuple(min(v[k] for v in pts) for k in range(3)))
    hi = Vector(tuple(max(v[k] for v in pts) for k in range(3)))
    box = [Vector((x, y, z)) for x, y, z in (
        (lo.x, lo.y, lo.z), (lo.x, lo.y, hi.z), (lo.x, hi.y, hi.z), (lo.x, hi.y, lo.z),
        (hi.x, lo.y, lo.z), (hi.x, lo.y, hi.z), (hi.x, hi.y, hi.z), (hi.x, hi.y, lo.z))]

    e_list = [(idx_map[e.vertices[0]], idx_map[e.vertices[1]]) for e in mesh.edges
              if e.vertices[0] in idx_map and e.vertices[1] in idx_map]
    base = len(verts)
    if len(e_list) > _EZ_MAX_GHOST_EDGES:
        verts.extend(box)
        edges.extend((base + a, base + b) for a, b in _EZ_BOX_EDGES)
    else:
        verts.extend(pts)
        edges.extend((base + a, base + b) for a, b in e_list)

    mesh.calc_loop_triangles()
    t_list = [tuple(idx_map[i] for i in lt.vertices) for lt in mesh.loop_triangles
              if all(i in idx_map for i in lt.vertices)]
    sbase = len(sverts)
    if len(t_list) > _EZ_MAX_SOLID_TRIS:
        sverts.extend(box)
        tris.extend((sbase + a, sbase + b, sbase + c) for a, b, c in _EZ_BOX_TRIS)
    else:
        sverts.extend(pts)
        tris.extend((sbase + a, sbase + b, sbase + c) for a, b, c in t_list)


def _ez_collect_ghost(arm_eval, bone_name, bone_world, depsgraph):
    """Geometry that follows the bone, in bone space:
    (verts, edges, solid_verts, tris). Heavy meshes fall back to a box."""
    inv = bone_world.inverted_safe()
    arm = arm_eval.original
    acc = ([], [], [], [])
    rigid = _ez_bound_objects(arm, bone_name)
    for obj in rigid:
        ce = obj.evaluated_get(depsgraph)
        mesh = ce.to_mesh()
        try:
            _ez_add_geometry(acc, mesh, inv @ ce.matrix_world)
        finally:
            ce.to_mesh_clear()
    for obj in _ez_skinned_objects(arm, bone_name, rigid):
        gi = obj.vertex_groups[bone_name].index
        keep = {v.index for v in obj.data.vertices
                for g in v.groups if g.group == gi and g.weight >= 0.5}
        if not keep:
            continue
        ce = obj.evaluated_get(depsgraph)
        mesh = ce.to_mesh()
        try:
            if len(mesh.vertices) == len(obj.data.vertices):   # same topology
                _ez_add_geometry(acc, mesh, inv @ ce.matrix_world, keep)
        finally:
            ce.to_mesh_clear()
    return acc


def _ez_sample(context, p):
    """Evaluate the bone at release-1 and release. Only raw matrices are kept,
    so the pivot offset can change later without re-evaluating the scene."""
    scene = context.scene
    _ez_unhide_real()               # hidden objects may skip evaluation
    _ez_state["sampling"] = True
    f = p.release_frame
    original = scene.frame_current
    mats = {}
    ghost = ([], [], [], [])
    tick = 0.05
    try:
        for fr in (f - 1, f):
            scene.frame_set(fr)
            dg = context.evaluated_depsgraph_get()
            ae = p.armature.evaluated_get(dg)
            pbe = ae.pose.bones[p.bone_name]
            mats[fr] = (ae.matrix_world @ pbe.matrix).copy()
            if fr == f:
                ghost = _ez_collect_ghost(ae, p.bone_name, mats[fr], dg)
                tick = max(pbe.length * 0.5, 0.01)
    finally:
        scene.frame_set(original)
        _ez_state["sampling"] = False
    return {"m_prev": mats[f - 1], "m_rel": mats[f], "tick": tick, "ghost": ghost}


def _ez_floor_contact(pos, vel, omega, p):
    """Resolve contact of the pivot with the floor plane z = floor_height.
    Called after every sub-step while 'Floor' is enabled.
    Returns the corrected (pos, vel, omega)."""
    if pos.z >= p.floor_height:
        return pos, vel, omega
    pos = Vector((pos.x, pos.y, p.floor_height))       # push out of the floor
    if vel.z < 0.0:
        vel_z = -vel.z * p.bounce
        if vel_z < 0.1:                                # settle instead of jittering
            vel_z = 0.0
        keep = 1.0 - p.friction                        # one loss per impact
        vel = Vector((vel.x * keep, vel.y * keep, vel_z))
        omega = omega * keep
    else:                                              # resting / sliding contact
        keep = max(0.0, 1.0 - p.friction * 0.05)
        vel = Vector((vel.x * keep, vel.y * keep, max(vel.z, 0.0)))
        omega = omega * keep
    return pos, vel, omega


def _ez_simulate(init, p, scene):
    """Integrate the flight of the PIVOT (bone matrix @ pivot_offset).
    The body spins around the pivot, so its velocity is measured there too."""
    fps = _ez_fps(scene)
    dt = 1.0 / (fps * _EZ_SUBSTEPS)
    c = Vector(p.pivot_offset)
    m0, m1 = init["m_prev"], init["m_rel"]

    _, q0, _ = m0.decompose()
    _, rot0, scale = m1.decompose()
    q0.make_compatible(rot0)
    init["scale"] = scale

    dq = rot0 @ q0.inverted()                    # world-space spin between frames
    axis, angle = dq.to_axis_angle()
    if angle > math.pi:
        angle -= 2.0 * math.pi

    pos = m1 @ c
    rot = rot0.copy()
    vel = ((pos - m0 @ c) * fps * p.inherit_velocity
           + rot0 @ Vector(p.extra_impulse))
    omega = (Vector(axis) * angle * fps * p.inherit_spin
             + rot0 @ Vector(p.extra_spin))
    g = Vector(scene.gravity) * p.gravity_scale
    lin_keep = max(0.0, 1.0 - p.linear_drag * dt)
    ang_keep = max(0.0, 1.0 - p.angular_damping * dt)

    frames = [(pos.copy(), rot.copy())]
    for _ in range(p.duration):
        for _ in range(_EZ_SUBSTEPS):
            vel = (vel + g * dt) * lin_keep          # semi-implicit Euler
            omega = omega * ang_keep
            pos = pos + vel * dt
            w = omega.length
            if w > 1e-8:
                rot = Quaternion(omega / w, w * dt) @ rot
                rot.normalize()
            if p.use_floor:
                pos, vel, omega = _ez_floor_contact(pos, vel, omega, p)
        frames.append((pos.copy(), rot.copy()))
    return frames, c


def _ez_world(init, pos, rot, offset):
    """Bone world matrix whose pivot (offset in bone space) sits at `pos`."""
    return Matrix.LocRotScale(pos, rot, init["scale"]) @ Matrix.Translation(-offset)


def _ez_refresh(context, resample):
    p = context.scene.bbutils_ezphyzi_props
    if not _ez_valid(p):
        _ez_state["init"] = _ez_state["frames"] = None
    else:
        if resample or _ez_state["init"] is None:
            _ez_state["init"] = _ez_sample(context, p)
        _ez_state["frames"], _ez_state["offset"] = _ez_simulate(
            _ez_state["init"], p, context.scene)
    _ez_state["batches"] = None
    _ez_state["ghosts"] = {}
    _ez_state["solids"] = {}
    _ez_apply_hide(context.scene)
    _redraw(context, {'VIEW_3D'})


# ── hiding the real mesh while the simulated one is shown ───────────────────

def _ez_unhide_real():
    for obj in _ez_state["hidden"]:
        try:
            obj.hide_set(False)
        except (ReferenceError, RuntimeError):
            pass
    _ez_state["hidden"] = []


def _ez_apply_hide(scene):
    """After the release frame the real mesh still sits in the hand (it is not
    baked yet) - hide it so only the simulated ghost is visible."""
    p = scene.bbutils_ezphyzi_props
    frames = _ez_state["frames"]
    if not (frames and p.hide_real_mesh and _ez_valid(p)):
        _ez_unhide_real()
        return
    in_flight = (_ez_state["launch"] is not None
                 or 0 <= scene.frame_current - p.release_frame < len(frames))
    if not in_flight:
        _ez_unhide_real()
        return
    for obj in _ez_bound_objects(p.armature, p.bone_name):
        if obj not in _ez_state["hidden"] and not obj.hide_get():
            try:
                obj.hide_set(True)
                _ez_state["hidden"].append(obj)
            except RuntimeError:        # not in the active view layer
                pass


def _ez_hide_timer():
    _ez_state["hide_pending"] = False
    if _ez_state["handle"] is not None and not _ez_state["sampling"]:
        try:
            _ez_apply_hide(bpy.context.scene)
        except (ReferenceError, AttributeError):
            pass
    return None                     # run once


@persistent
def _ez_on_frame_change(scene, *_args):
    # NEVER touch visibility here: frame_change_post runs while the depsgraph
    # is being evaluated and hide_set() there crashes Blender
    # (deg_check_base_in_depsgraph). Defer it to the main loop instead.
    if (_ez_state["handle"] is not None and not _ez_state["sampling"]
            and not _ez_state["hide_pending"]):
        _ez_state["hide_pending"] = True
        bpy.app.timers.register(_ez_hide_timer, first_interval=0.0)


# ── drawing ─────────────────────────────────────────────────────────────────

def _ez_ghost_batch(shader, idx):
    cache = _ez_state["ghosts"]
    if idx not in cache:
        init = _ez_state["init"]
        verts, edges = init["ghost"][0], init["ghost"][1]
        if not edges:
            cache[idx] = None
        else:
            m = _ez_world(init, *_ez_state["frames"][idx], _ez_state["offset"])
            cache[idx] = batch_for_shader(shader, 'LINES',
                                          {"pos": [m @ v for v in verts]},
                                          indices=edges)
    return cache[idx]


def _ez_solid_batch(shader, idx):
    """Flat-shaded solid copy of the mesh (lit by a fixed world light)."""
    cache = _ez_state["solids"]
    if idx not in cache:
        init = _ez_state["init"]
        sverts, tris = init["ghost"][2], init["ghost"][3]
        if not tris:
            cache[idx] = None
        else:
            m = _ez_world(init, *_ez_state["frames"][idx], _ez_state["offset"])
            world = [m @ v for v in sverts]
            pos, col = [], []
            r, g, b = _EZ_COL_SOLID
            for a, bb, c in tris:
                va, vb, vc = world[a], world[bb], world[c]
                n = (vb - va).cross(vc - va)
                n = n.normalized() if n.length_squared > 1e-20 else n
                k = 0.35 + 0.65 * abs(n.dot(_EZ_LIGHT_DIR))
                pos += (va, vb, vc)
                col += [(r * k, g * k, b * k, 1.0)] * 3
            cache[idx] = batch_for_shader(shader, 'TRIS', {"pos": pos, "color": col})
    return cache[idx]


def _ez_current_index(p, scene):
    """Simulation frame to show: the launch, or the timeline position."""
    if _ez_state["launch"] is not None:
        return _ez_state["launch"]
    return scene.frame_current - p.release_frame


def _ez_pivot_now(p, scene):
    """Where the pivot is on the current frame: simulated once released,
    otherwise taken live from the posed bone."""
    frames = _ez_state["frames"]
    idx = _ez_current_index(p, scene)
    if frames and 0 <= idx < len(frames):
        return frames[idx][0]
    arm = p.armature
    return arm.matrix_world @ arm.pose.bones[p.bone_name].matrix @ Vector(p.pivot_offset)


def _ez_draw_callback():
    frames, init = _ez_state["frames"], _ez_state["init"]
    if not frames or not init:
        return
    space = bpy.context.space_data
    overlay = getattr(space, "overlay", None)
    # with Overlays / Extras off only the grey stand-in mesh is drawn: it
    # replaces the hidden real mesh, so it must stay visible like geometry
    helpers = overlay is None or (overlay.show_overlays
                                  and getattr(overlay, "show_extras", True))
    scene = bpy.context.scene
    p = scene.bbutils_ezphyzi_props
    if not _ez_valid(p):
        return

    line_sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
    point_sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
    solid_sh = gpu.shader.from_builtin('SMOOTH_COLOR')

    if _ez_state["batches"] is None:
        path = [pos for pos, _ in frames]
        ticks = []
        for pos, rot in frames:
            ticks += [pos, pos + rot @ Vector((0.0, init["tick"], 0.0))]
        _ez_state["batches"] = {
            "path":  batch_for_shader(line_sh, 'LINE_STRIP', {"pos": path}),
            "ticks": batch_for_shader(line_sh, 'LINES', {"pos": ticks}),
            "dots":  batch_for_shader(point_sh, 'POINTS', {"pos": path}),
        }
    b = _ez_state["batches"]
    last = len(frames) - 1
    now = _ez_current_index(p, scene)
    launching = _ez_state["launch"] is not None

    # solid ghost on the current frame - depth tested, so hands/gun occlude it
    if 0 <= now <= last:
        batch = _ez_solid_batch(solid_sh, now)
        if batch is not None:
            gpu.state.depth_test_set('LESS_EQUAL')
            gpu.state.depth_mask_set(True)
            solid_sh.bind()
            batch.draw(solid_sh)
            gpu.state.depth_mask_set(False)

    if not helpers:
        gpu.state.depth_test_set('NONE')
        return

    gpu.state.blend_set('ALPHA')
    # path, ghosts and pivot are hidden behind scene geometry unless In Front
    gpu.state.depth_test_set('NONE' if p.show_in_front else 'LESS_EQUAL')
    vp = gpu.state.viewport_get()

    line_sh.bind()
    line_sh.uniform_float("viewportSize", (float(vp[2]), float(vp[3])))

    def draw_lines(batch, color, width):
        if batch is None:
            return
        line_sh.uniform_float("lineWidth", width)
        line_sh.uniform_float("color", color)
        batch.draw(line_sh)

    draw_lines(b["path"], _EZ_COL_PATH, 2.5)
    if not launching:                       # keep the launch view clean
        draw_lines(b["ticks"], _EZ_COL_TICK, 1.5)
        for idx in sorted(set(range(0, last + 1, p.ghost_step)) | {last}):
            draw_lines(_ez_ghost_batch(line_sh, idx), _EZ_COL_GHOST, 1.0)
    if 0 <= now <= last and _ez_solid_batch(solid_sh, now) is None:
        draw_lines(_ez_ghost_batch(line_sh, now), _EZ_COL_NOW, 1.0)

    point_sh.bind()
    if not launching:
        point_sh.uniform_float("color", _EZ_COL_PATH)
        gpu.state.point_size_set(5.0)
        b["dots"].draw(point_sh)

    # pivot dot: dark rim + yellow core
    pivot = batch_for_shader(point_sh, 'POINTS', {"pos": [_ez_pivot_now(p, scene)]})
    gpu.state.point_size_set(13.0)
    point_sh.uniform_float("color", (0.0, 0.0, 0.0, 0.9))
    pivot.draw(point_sh)
    gpu.state.point_size_set(9.0)
    point_sh.uniform_float("color", _EZ_COL_PIVOT)
    pivot.draw(point_sh)

    gpu.state.point_size_set(1.0)
    gpu.state.blend_set('NONE')
    gpu.state.depth_test_set('NONE')


def _ez_preview_on(context):
    if _ez_state["handle"] is None:
        _ez_state["handle"] = bpy.types.SpaceView3D.draw_handler_add(
            _ez_draw_callback, (), 'WINDOW', 'POST_VIEW')
    if _ez_on_frame_change not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(_ez_on_frame_change)
    _ez_refresh(context, resample=True)


def _ez_preview_off(context=None):
    _ez_state["launch"] = None
    if _ez_state["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_ez_state["handle"], 'WINDOW')
    if _ez_on_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_ez_on_frame_change)
    _ez_unhide_real()
    _ez_state.update(handle=None, init=None, frames=None, batches=None,
                     ghosts={}, solids={})
    if context is not None:
        _redraw(context, {'VIEW_3D'})


@persistent
def _ez_on_load_pre(*_args):
    _ez_state["hidden"] = []        # objects belong to the old file
    _ez_preview_off()


class BBUTILS_OT_ezphyzi_from_active(Operator):
    """Use the active pose bone as the thrown object, release it on the current
    frame and show its flight path in the viewport"""
    bl_idname = "bbutils.ezphyzi_from_active"
    bl_label = "EzPhyzi It"
    bl_options = {'REGISTER'}

    @staticmethod
    def _active_bone(context):
        obj = context.object
        if obj is None or obj.type != 'ARMATURE' or obj.mode != 'POSE':
            return None
        pb = getattr(context, "active_pose_bone", None)
        if pb is None and obj.data.bones.active is not None:
            pb = obj.pose.bones.get(obj.data.bones.active.name)
        return pb

    @classmethod
    def poll(cls, context):
        return cls._active_bone(context) is not None

    def execute(self, context):
        p = context.scene.bbutils_ezphyzi_props
        p.armature = context.object
        p.bone_name = self._active_bone(context).name
        p.release_frame = context.scene.frame_current
        _ez_preview_on(context)
        return {'FINISHED'}


class BBUTILS_OT_ezphyzi_toggle_preview(Operator):
    """Show / hide the EzPhyzi flight preview"""
    bl_idname = "bbutils.ezphyzi_toggle_preview"
    bl_label = "Toggle Trajectory Preview"

    def execute(self, context):
        p = context.scene.bbutils_ezphyzi_props
        if _ez_state["handle"] is not None:
            _ez_preview_off(context)
        elif not _ez_valid(p):
            self.report({'WARNING'}, "Pick an armature and a bone first")
            return {'CANCELLED'}
        else:
            _ez_preview_on(context)
        return {'FINISHED'}


class BBUTILS_OT_ezphyzi_play(Operator):
    """Launch the object once along the simulated path in real time, like a
    particle. The timeline is not touched. Click again to relaunch, Esc to stop"""
    bl_idname = "bbutils.ezphyzi_play"
    bl_label = "Launch Preview"

    _instance = None        # running launch, so a second click restarts it

    @classmethod
    def poll(cls, context):
        return _ez_valid(context.scene.bbutils_ezphyzi_props)

    def invoke(self, context, event):
        if _ez_state["handle"] is None:
            _ez_preview_on(context)
        elif _ez_state["init"] is None:
            _ez_refresh(context, resample=True)
        if not _ez_state["frames"]:
            return {'CANCELLED'}

        running = BBUTILS_OT_ezphyzi_play._instance
        if running is not None:             # restart the running launch
            running._t0 = time.perf_counter()
            _ez_state["launch"] = 0
            return {'CANCELLED'}

        self._t0 = time.perf_counter()
        self._fps = _ez_fps(context.scene)
        _ez_state["launch"] = 0
        _ez_apply_hide(context.scene)
        wm = context.window_manager
        self._timer = wm.event_timer_add(1.0 / 120.0, window=context.window)
        wm.modal_handler_add(self)
        BBUTILS_OT_ezphyzi_play._instance = self
        _redraw(context, {'VIEW_3D'})
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        context.window_manager.event_timer_remove(self._timer)
        BBUTILS_OT_ezphyzi_play._instance = None
        _ez_state["launch"] = None
        _ez_apply_hide(context.scene)
        _redraw(context, {'VIEW_3D'})

    def modal(self, context, event):
        frames = _ez_state["frames"]
        if (_ez_state["handle"] is None or not frames
                or _ez_state["launch"] is None or event.type == 'ESC'):
            self._finish(context)
            return {'CANCELLED'}
        if event.type == 'TIMER':
            idx = int((time.perf_counter() - self._t0) * self._fps)
            if idx >= len(frames):
                self._finish(context)
                return {'FINISHED'}
            if idx != _ez_state["launch"]:
                _ez_state["launch"] = idx
                _redraw(context, {'VIEW_3D'})
        return {'PASS_THROUGH'}         # orbit / zoom keep working


class BBUTILS_OT_ezphyzi_center_pivot(Operator):
    """Put the pivot in the middle of the bone's mesh (bounding-box centre)"""
    bl_idname = "bbutils.ezphyzi_center_pivot"
    bl_label = "Pivot To Mesh Centre"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _ez_valid(context.scene.bbutils_ezphyzi_props)

    def execute(self, context):
        p = context.scene.bbutils_ezphyzi_props
        init = _ez_state["init"] or _ez_sample(context, p)
        verts = init["ghost"][0]
        if not verts:
            self.report({'WARNING'}, f"No mesh is parented to bone '{p.bone_name}'")
            return {'CANCELLED'}
        lo = Vector(tuple(min(v[i] for v in verts) for i in range(3)))
        hi = Vector(tuple(max(v[i] for v in verts) for i in range(3)))
        p.pivot_offset = (lo + hi) * 0.5
        return {'FINISHED'}


class BBUTILS_OT_ezphyzi_resample(Operator):
    """Re-read the bone animation (use after editing the throw animation)"""
    bl_idname = "bbutils.ezphyzi_resample"
    bl_label = "Resample Animation"

    @classmethod
    def poll(cls, context):
        return _ez_state["handle"] is not None

    def execute(self, context):
        _ez_refresh(context, resample=True)
        return {'FINISHED'}


class BBUTILS_OT_ezphyzi_bake(Operator):
    """Bake the previewed flight onto the bone (one key per frame,
    from the release frame on) and turn its constraints off after release"""
    bl_idname = "bbutils.ezphyzi_bake"
    bl_label = "Bake EzPhyzi"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _ez_valid(context.scene.bbutils_ezphyzi_props)

    def _disable_constraints(self, scene, arm, pb, start, end):
        for con in pb.constraints:
            if not hasattr(con, "influence"):
                continue
            dp = f'pose.bones["{pb.name}"].constraints["{con.name}"].influence'
            ad = arm.animation_data
            if ad and ad.action:                       # drop old keys in range
                for fc in _get_action_fcurves(ad.action, arm):
                    if fc.data_path == dp:
                        for kp in reversed(list(fc.keyframe_points)):
                            if start <= kp.co[0] <= end:
                                fc.keyframe_points.remove(kp)
            scene.frame_set(start - 1)
            con.keyframe_insert("influence", frame=start - 1)
            con.influence = 0.0
            con.keyframe_insert("influence", frame=start)

    def execute(self, context):
        scene = context.scene
        p = scene.bbutils_ezphyzi_props
        arm = p.armature
        pb = arm.pose.bones[p.bone_name]

        if pb.bone.use_connect:
            self.report({'ERROR'}, f"'{pb.name}' is connected to its parent - "
                                   "disconnect it so it can move freely")
            return {'CANCELLED'}

        init = _ez_sample(context, p)                  # always bake fresh data
        frames, offset = _ez_simulate(init, p, scene)
        kt = p.key_type
        start = p.release_frame
        end = start + len(frames) - 1
        original = scene.frame_current

        try:
            if p.key_constraints_off:
                self._disable_constraints(scene, arm, pb, start, end)

            prev_e, prev_q = None, None
            for i, (pos, rot) in enumerate(frames):
                f = start + i
                scene.frame_set(f)
                pb.matrix = arm.matrix_world.inverted() @ _ez_world(init, pos, rot, offset)

                pb.keyframe_insert("location", frame=f, keytype=kt)
                mode = pb.rotation_mode
                if mode == 'QUATERNION':
                    q = pb.rotation_quaternion.copy()
                    if prev_q is not None and q.dot(prev_q) < 0.0:
                        q.negate()                      # no hemisphere flips
                    pb.rotation_quaternion = prev_q = q
                    pb.keyframe_insert("rotation_quaternion", frame=f, keytype=kt)
                elif mode == 'AXIS_ANGLE':
                    pb.keyframe_insert("rotation_axis_angle", frame=f, keytype=kt)
                else:
                    rot3 = pb.matrix_basis.to_3x3().normalized()
                    e = rot3.to_euler(mode, prev_e) if prev_e else rot3.to_euler(mode)
                    pb.rotation_euler = prev_e = e      # no 360 degree jumps
                    pb.keyframe_insert("rotation_euler", frame=f, keytype=kt)
                pb.keyframe_insert("scale", frame=f, keytype=kt)
        finally:
            scene.frame_set(original)

        _ez_preview_off(context)
        self.report({'INFO'}, f"EzPhyzi baked on '{pb.name}', "
                              f"frames {start}–{end}")
        _redraw(context, {'VIEW_3D', 'DOPESHEET_EDITOR', 'GRAPH_EDITOR'})
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 4 — F-CURVE TOOLS
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_OT_flip_fcurve(Operator):
    """Flip selected F-Curves vertically, preserving the value of the first
    and last keyframe."""
    bl_idname = "bbutils.flip_fcurve"
    bl_label = "Flip F-Curve (Preserve First & Last)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and
                context.active_object.animation_data and
                context.active_object.animation_data.action)

    def execute(self, context):
        fcurves = _get_action_fcurves(context.active_object.animation_data.action,
                                      context.active_object)
        flipped = 0

        for fc in fcurves:
            if not fc.select or len(fc.keyframe_points) < 2:
                continue

            k     = fc.keyframe_points
            pivot = (k[0].co.y + k[-1].co.y) * 0.5

            for kp in k:
                kp.co.y           = pivot - (kp.co.y           - pivot)
                kp.handle_left.y  = pivot - (kp.handle_left.y  - pivot)
                kp.handle_right.y = pivot - (kp.handle_right.y - pivot)

            if hasattr(fc, "update"):
                fc.update()
            flipped += 1

        if flipped == 0:
            self.report({'WARNING'}, "No selected F-Curves with 2+ keypoints found")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Flipped {flipped} F-Curve(s)")
        _redraw(context, {'GRAPH_EDITOR'})
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 5 — BATCH VIEWPORT RENDERER
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_OT_batch_viewport_render(Operator):
    """Render every action as a separate viewport-shaded MP4 video."""
    bl_idname = "bbutils.batch_viewport_render"
    bl_label = "Render All Actions"
    bl_options = {'REGISTER'}

    @staticmethod
    def _sanitize(name):
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, '_')
        return name

    def execute(self, context):
        scene = context.scene
        props = scene.bbutils_render_props

        armature = props.armature_object or next(
            (o for o in scene.objects if o.type == 'ARMATURE'), None
        )
        if not armature:
            self.report({'ERROR'}, "No armature found")
            return {'CANCELLED'}

        rig_actions, no_slot = _rig_actions(armature)
        all_actions = [a for a in rig_actions if a.users > 0]
        filt    = props.action_filter.strip()
        actions = ([a for a in all_actions if filt.lower() in a.name.lower()]
                   if filt else all_actions)

        if not actions:
            self.report({'ERROR'},
                        "No actions found" + (f" matching '{filt}'" if filt else ""))
            return {'CANCELLED'}

        # Validate everything BEFORE touching any scene settings
        view3d_area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
        if not view3d_area:
            self.report({'ERROR'}, "No 3D Viewport found on current screen")
            return {'CANCELLED'}
        region = next((r for r in view3d_area.regions if r.type == 'WINDOW'), None)
        if not region:
            self.report({'ERROR'}, "No WINDOW region in 3D Viewport")
            return {'CANCELLED'}

        anim = _ensure_animation_data(armature)
        if anim.use_tweak_mode:
            self.report({'ERROR'}, "Exit NLA tweak mode first")
            return {'CANCELLED'}

        output_dir = props.output_folder or (
            os.path.join(Path(bpy.data.filepath).parent, "rendered_actions")
            if bpy.data.filepath else
            os.path.join(Path.home(), "rendered_actions")
        )
        output_dir = bpy.path.abspath(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        render = scene.render
        saved = {
            'frame':      scene.frame_current,
            'start':      scene.frame_start,
            'end':        scene.frame_end,
            'action':     anim.action,
            'slot':       getattr(anim, "action_slot", None),
            'filepath':   render.filepath,
            'format':     render.image_settings.file_format,
            'res_x':      render.resolution_x,
            'res_y':      render.resolution_y,
            'res_pct':    render.resolution_percentage,
            'camera':     scene.camera,
        }

        render.resolution_x          = props.resolution_x
        render.resolution_y          = props.resolution_y
        render.resolution_percentage = props.resolution_percentage
        if props.camera_object:
            scene.camera = props.camera_object

        render.image_settings.file_format  = 'FFMPEG'
        render.ffmpeg.format               = 'MPEG4'
        render.ffmpeg.codec                = 'H264'
        render.ffmpeg.constant_rate_factor = 'MEDIUM'
        render.ffmpeg.ffmpeg_preset        = 'GOOD'

        wm = context.window_manager
        wm.progress_begin(0, len(actions))
        rendered = 0

        try:
            for i, action in enumerate(actions):
                wm.progress_update(i)
                self.report({'INFO'},
                            f"[{i + 1}/{len(actions)}] Rendering: {action.name}")

                _assign_action(armature, action)

                frame_start = int(action.frame_range[0])
                frame_end   = int(action.frame_range[1])
                if props.use_custom_range:
                    frame_start = props.frame_start
                    frame_end   = props.frame_end
                frame_end = max(frame_end, frame_start)

                scene.frame_start = frame_start
                scene.frame_end   = frame_end
                render.filepath   = os.path.join(
                    output_dir, f"{self._sanitize(action.name)}.mp4")

                with context.temp_override(area=view3d_area, region=region):
                    bpy.ops.render.opengl(animation=True, view_context=True)
                rendered += 1

        finally:
            wm.progress_end()
            _assign_action(armature, saved['action'])
            if saved['slot'] is not None and saved['action'] is not None:
                try:
                    armature.animation_data.action_slot = saved['slot']
                except Exception:
                    pass
            scene.frame_current            = saved['frame']
            scene.frame_start              = saved['start']
            scene.frame_end                = saved['end']
            render.filepath                = saved['filepath']
            render.image_settings.file_format = saved['format']
            render.resolution_x            = saved['res_x']
            render.resolution_y            = saved['res_y']
            render.resolution_percentage   = saved['res_pct']
            scene.camera                   = saved['camera']

        msg = f"Done - rendered {rendered} action(s) to: {output_dir}"
        if no_slot:
            msg += f" ({no_slot} skipped: no slot for this rig)"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ─────────────────────────────────────────────────────────────────────────────
#  SECTION 6 — TAB HUB (move other add-ons' sidebar panels into one tab)
# ─────────────────────────────────────────────────────────────────────────────
#
#  A sidebar tab is just Panel.bl_category, read when the class is registered.
#  The hub re-registers chosen panels (with their sub-panels) under one
#  category. A chosen SUB-panel is lifted out of its parent (bl_parent_id
#  cleared) so a single section can live in the hub on its own.
#  The list lives in the add-on preferences; a light watchdog re-applies it
#  when other add-ons (re)register their panels later.

_HUB_SPACES = [
    ('VIEW_3D',           "3D Viewport",  ""),
    ('DOPESHEET_EDITOR',  "Dope Sheet",   ""),
    ('GRAPH_EDITOR',      "Graph Editor", ""),
    ('NLA_EDITOR',        "NLA Editor",   ""),
    ('NODE_EDITOR',       "Node Editor",  ""),
    ('IMAGE_EDITOR',      "Image Editor", ""),
    ('SEQUENCE_EDITOR',   "Sequencer",    ""),
    ('CLIP_EDITOR',       "Movie Clip",   ""),
    ('TEXT_EDITOR',       "Text Editor",  ""),
]
_HUB_WATCH_INTERVAL = 2.0
_hub_orig = {}          # panel id -> {"category", "parent", "order"} before moving
_hub_clones = {}        # panel id -> {"src", "classes", "space", "hub", "order"}
_HUB_SKIP_ATTRS = {"__dict__", "__weakref__", "__module__", "__qualname__",
                   "__doc__", "bl_rna", "__slots__", "__annotations__"}


def _hub_prefs():
    addon = bpy.context.preferences.addons.get(__name__)
    return addon.preferences if addon else None


def _hub_pid(cls):
    return getattr(cls, "bl_idname", "") or cls.__name__


def _hub_all_panels():
    """Every registered sidebar (UI region) panel class."""
    out, stack, seen = [], list(bpy.types.Panel.__subclasses__()), set()
    while stack:
        cls = stack.pop(0)              # keep definition order
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
        registered = getattr(cls, "is_registered", "bl_rna" in cls.__dict__)
        if registered and getattr(cls, "bl_region_type", "") == 'UI':
            out.append(cls)
    return out


def _hub_is_ours(cls):
    return cls.__module__ == __name__ and _hub_pid(cls).startswith("BBUTILS_PT")


def _hub_label(cls):
    """Readable name; many sub-panels leave bl_label empty and draw their
    title in draw_header, so fall back to the class name."""
    return getattr(cls, "bl_label", "") or cls.__name__


def _hub_find(pid, panels=None):
    for cls in panels or _hub_all_panels():
        if _hub_pid(cls) == pid:
            return cls
    return None


def _hub_descendants(cls, panels):
    out, queue = [], [_hub_pid(cls)]
    while queue:
        parent = queue.pop(0)
        for c in panels:
            if getattr(c, "bl_parent_id", "") == parent and c not in out:
                out.append(c)
                queue.append(_hub_pid(c))
    return out


def _hub_reregister(cls, mutate, panels):
    """Unregister a panel + its sub-panels, change it, register them back."""
    kids = _hub_descendants(cls, panels)
    for c in reversed(kids):
        if getattr(c, "is_registered", True):
            bpy.utils.unregister_class(c)
    bpy.utils.unregister_class(cls)
    try:
        mutate(cls)
    finally:
        bpy.utils.register_class(cls)
        for c in kids:
            try:
                bpy.utils.register_class(c)
            except Exception:
                pass


def _hub_original_category(cls):
    pid = _hub_pid(cls)
    if pid in _hub_orig:
        return _hub_orig[pid]["category"] or "(sub-panel)"
    return getattr(cls, "bl_category", "") or "Misc"


def _hub_move(cls, hub, order, panels):
    pid = _hub_pid(cls)
    if pid not in _hub_orig:
        _hub_orig[pid] = {
            "category": getattr(cls, "bl_category", ""),
            "parent":   getattr(cls, "bl_parent_id", ""),
            "order":    getattr(cls, "bl_order", 0),
        }

    def mutate(c):
        c.bl_category = hub
        c.bl_parent_id = ""         # a lifted section becomes a top panel
        c.bl_order = order
    _hub_reregister(cls, mutate, panels)


def _hub_restore(cls, panels):
    orig = _hub_orig.pop(_hub_pid(cls), None)
    if orig is None:
        return
    parent = orig["parent"]
    if parent and _hub_find(parent, panels) is None:
        return                      # parent is gone - leave it where it is

    def mutate(c):
        c.bl_category = orig["category"] or "Misc"
        c.bl_parent_id = parent
        c.bl_order = orig["order"]
    _hub_reregister(cls, mutate, panels)


def _hub_orig_parent(cls):
    pid = _hub_pid(cls)
    if pid in _hub_orig:
        return _hub_orig[pid]["parent"]
    return getattr(cls, "bl_parent_id", "")


def _hub_clone_id(pid):
    return f"BBUTILS_PT_hub_{zlib.crc32(pid.encode()) & 0xffffffff:08x}"


def _hub_clone_class(src, cid, space, category, parent, order):
    """A brand-new Panel class carrying src's draw / poll / helpers, but living
    in another editor. The original panel stays where it was."""
    attrs = {}
    for klass in reversed(src.__mro__):
        if klass in bpy.types.Panel.__mro__:
            continue
        for key, value in klass.__dict__.items():
            if key not in _HUB_SKIP_ATTRS:
                attrs[key] = value
    attrs.update(
        bl_idname=cid, bl_space_type=space, bl_region_type='UI',
        bl_category=category, bl_parent_id=parent, bl_order=order,
        __module__=__name__,
    )
    return type(cid, (bpy.types.Panel,), attrs)


def _hub_clone_tree(src, space, hub, order, panels, parent_cid=""):
    """Clone a panel and (recursively) its sub-panels into `space`."""
    cid = _hub_clone_id(_hub_pid(src))
    new = _hub_clone_class(src, cid, space, hub, parent_cid, order)
    bpy.utils.register_class(new)
    out = [new]
    for kid in panels:
        if _hub_orig_parent(kid) == _hub_pid(src) and kid.bl_space_type == src.bl_space_type:
            try:
                out += _hub_clone_tree(kid, space, hub, getattr(kid, "bl_order", 0),
                                       panels, cid)
            except Exception as exc:
                print(f"BBUtils Tab Hub: could not clone {_hub_pid(kid)}: {exc}")
    return out


def _hub_drop_clone(pid):
    info = _hub_clones.pop(pid, None)
    if not info:
        return
    for c in reversed(info["classes"]):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass


def _hub_mode(cls, prefs):
    """('move', space) when the hub is in the panel's own editor,
    ('clone', target) when it has to be copied into another editor."""
    target = prefs.hub_space
    if target == 'SAME' or target == cls.bl_space_type:
        return ('move', cls.bl_space_type)
    return ('clone', target)


def _hub_bring_to_front(space, hub):
    """Sidebar tabs appear in panel REGISTRATION order (bl_order does not
    change it), and freshly re-registered hub panels end up last. Re-register
    every other top-level panel of the editor so they queue up behind the hub,
    keeping their relative order."""
    panels = _hub_all_panels()
    others = [c for c in panels
              if c.bl_space_type == space and not getattr(c, "bl_parent_id", "")
              and getattr(c, "bl_category", "") != hub]
    for cls in others:
        try:
            _hub_reregister(cls, lambda _c: None, panels)
        except Exception as exc:
            print(f"BBUtils Tab Hub: could not reorder {_hub_pid(cls)}: {exc}")


def _hub_needs_move(cls, hub, order):
    return (getattr(cls, "bl_category", "") != hub
            or getattr(cls, "bl_parent_id", "")
            or getattr(cls, "bl_order", 0) != order)


def _hub_apply():
    """Bring the registered panels in line with the preferences."""
    prefs = _hub_prefs()
    if prefs is None:
        return
    hub = prefs.hub_name.strip() or "Hub"
    wanted = {e.panel_id: i for i, e in enumerate(prefs.hub_entries)}
    panels = _hub_all_panels()
    desired = {}
    for pid in wanted:
        cls = _hub_find(pid, panels)
        if cls is not None:
            desired[pid] = (cls, _hub_mode(cls, prefs))

    # 1. drop clones that are no longer wanted or are out of date
    for pid, info in list(_hub_clones.items()):
        want = desired.get(pid)
        if (want is None or want[1] != ('clone', info["space"])
                or want[0] is not info["src"] or info["hub"] != hub
                or info["order"] != wanted[pid]):
            _hub_drop_clone(pid)
    panels = _hub_all_panels()

    # 2. send moved panels back if they are no longer moved
    for pid in [p for p in _hub_orig if desired.get(p, (None, (None,)))[1][0] != 'move']:
        cls = _hub_find(pid, panels)
        if cls is None:
            _hub_orig.pop(pid, None)
        else:
            try:
                _hub_restore(cls, panels)
            except Exception as exc:
                print(f"BBUtils Tab Hub: could not restore {pid}: {exc}")
            panels = _hub_all_panels()

    changed_spaces = set()
    for pid, order in wanted.items():
        if pid not in desired:
            continue
        cls = _hub_find(pid, panels)
        mode, space = desired[pid][1]
        try:
            if mode == 'move':
                if _hub_needs_move(cls, hub, order):
                    _hub_move(cls, hub, order, panels)
                    changed_spaces.add(space)
            elif pid not in _hub_clones:
                classes = _hub_clone_tree(cls, space, hub, order, panels)
                _hub_clones[pid] = {"src": cls, "classes": classes,
                                    "space": space, "hub": hub, "order": order}
                changed_spaces.add(space)
        except Exception as exc:
            print(f"BBUtils Tab Hub: could not hub {pid}: {exc}")
        panels = _hub_all_panels()

    for space in changed_spaces:
        _hub_bring_to_front(space, hub)

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _hub_restore_all():
    for pid in list(_hub_clones):
        _hub_drop_clone(pid)
    panels = _hub_all_panels()
    for pid in list(_hub_orig):
        cls = _hub_find(pid, panels)
        if cls is not None:
            try:
                _hub_restore(cls, panels)
            except Exception:
                pass
            panels = _hub_all_panels()
    _hub_orig.clear()


def _hub_watchdog():
    """Other add-ons may register / reload their panels at any time."""
    try:
        prefs = _hub_prefs()
        if prefs is not None and len(prefs.hub_entries):
            hub = prefs.hub_name.strip() or "Hub"
            panels = _hub_all_panels()
            for i, e in enumerate(prefs.hub_entries):
                cls = _hub_find(e.panel_id, panels)
                if cls is None:
                    continue
                mode, _space = _hub_mode(cls, prefs)
                info = _hub_clones.get(e.panel_id)
                stale = (_hub_needs_move(cls, hub, i) if mode == 'move'
                         else info is None or info["src"] is not cls)
                if stale:
                    _hub_apply()
                    break
    except Exception as exc:
        print(f"BBUtils Tab Hub watchdog: {exc}")
    return _HUB_WATCH_INTERVAL


def _hub_on_name(self, context):
    _hub_apply()


class BBUtilsHubEntry(PropertyGroup):
    panel_id: StringProperty(name="Panel ID")
    label: StringProperty(name="Label")
    space: StringProperty(name="Editor")


class BBUtilsPreferences(AddonPreferences):
    bl_idname = __name__

    hub_name: StringProperty(
        name="Hub Tab Name", default="Hub",
        description="Sidebar tab that collects the hubbed panels",
        update=_hub_on_name,
    )
    hub_space: EnumProperty(
        name="Hub In",
        description="Editor whose sidebar holds the hub tab. Panels from other "
                    "editors are copied there; the originals stay in place",
        items=[('SAME', "Each Panel's Own Editor",
                "Move panels into a hub tab inside their own editor")] + _HUB_SPACES,
        default='SAME',
        update=_hub_on_name,
    )
    hub_entries: CollectionProperty(type=BBUtilsHubEntry)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "hub_name")
        layout.prop(self, "hub_space")
        box = layout.box()
        box.label(text=f"Panels in the hub ({len(self.hub_entries)})", icon='WINDOW')
        if not len(self.hub_entries):
            box.label(text="Nothing yet - open Tab Hub from any BBUtils panel")
        for e in self.hub_entries:
            row = box.row(align=True)
            row.label(text=f"{e.label}   [{e.space}]")
            op = row.operator("bbutils.tabhub_toggle", text="", icon='X', emboss=False)
            op.panel_id = e.panel_id


class BBUTILS_OT_tabhub_toggle(Operator):
    """Move this panel (or section) into the hub tab, or send it back"""
    bl_idname = "bbutils.tabhub_toggle"
    bl_label = "Toggle Panel In Hub"

    panel_id: StringProperty()

    def execute(self, context):
        prefs = _hub_prefs()
        if prefs is None:
            self.report({'ERROR'}, "BBUtils preferences not available")
            return {'CANCELLED'}
        for i, e in enumerate(prefs.hub_entries):
            if e.panel_id == self.panel_id:
                prefs.hub_entries.remove(i)
                break
        else:
            cls = _hub_find(self.panel_id)
            if cls is None:
                return {'CANCELLED'}
            e = prefs.hub_entries.add()
            e.panel_id = self.panel_id
            e.label = _hub_label(cls)
            e.space = getattr(cls, "bl_space_type", "")
        _hub_apply()
        context.preferences.is_dirty = True
        return {'FINISHED'}


class BBUTILS_OT_tabhub_move(Operator):
    """Change the order of a panel inside the hub"""
    bl_idname = "bbutils.tabhub_move"
    bl_label = "Move Hub Panel"

    panel_id: StringProperty()
    direction: IntProperty(default=-1)

    def execute(self, context):
        prefs = _hub_prefs()
        entries = prefs.hub_entries
        idx = next((i for i, e in enumerate(entries) if e.panel_id == self.panel_id), -1)
        new = idx + self.direction
        if idx < 0 or not 0 <= new < len(entries):
            return {'CANCELLED'}
        entries.move(idx, new)
        _hub_apply()
        context.preferences.is_dirty = True
        return {'FINISHED'}


class BBUTILS_OT_tabhub_manager(Operator):
    """Pick panels and sections of other add-ons to collect into one tab"""
    bl_idname = "bbutils.tabhub_manager"
    bl_label = "Tab Hub"

    space: EnumProperty(name="Editor", items=_HUB_SPACES)
    search: StringProperty(name="Search", options={'TEXTEDIT_UPDATE'})

    def invoke(self, context, event):
        area = context.area.type if context.area else 'VIEW_3D'
        if area in {k for k, _, _ in _HUB_SPACES}:
            self.space = area
        return context.window_manager.invoke_props_dialog(self, width=560)

    def execute(self, context):
        return {'FINISHED'}

    def _row(self, layout, cls, hubbed, indent):
        pid = _hub_pid(cls)
        row = layout.row(align=True)
        if indent:
            row.separator(factor=2.5 * indent)
        on = pid in hubbed
        op = row.operator("bbutils.tabhub_toggle", text="",
                          icon='CHECKBOX_HLT' if on else 'CHECKBOX_DEHLT', emboss=False)
        op.panel_id = pid
        label = _hub_label(cls)
        row.label(text=label if not indent else f"{label}  (section)")
        addon = cls.__module__.split(".")[-1]
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.enabled = False
        sub.label(text=addon)

    def draw(self, context):
        layout = self.layout
        prefs = _hub_prefs()
        if prefs is None:
            layout.label(text="BBUtils preferences not available", icon='ERROR')
            return
        hubbed = {e.panel_id for e in prefs.hub_entries}

        row = layout.row(align=True)
        row.prop(prefs, "hub_name", text="Tab")
        row.prop(prefs, "hub_space", text="In")
        row = layout.row(align=True)
        row.label(text="Browse:")
        row.prop(self, "space", text="")
        layout.prop(self, "search", text="", icon='VIEWZOOM')

        panels = [c for c in _hub_all_panels()
                  if c.bl_space_type == self.space and not _hub_is_ours(c)]
        by_id = {_hub_pid(c): c for c in panels}

        # ── panels already in the hub, in hub order ──
        if prefs.hub_space == 'SAME':
            in_hub = [e for e in prefs.hub_entries if e.space == self.space]
        else:
            in_hub = list(prefs.hub_entries)
        names = {k: n for k, n, _ in _HUB_SPACES}
        if in_hub:
            box = layout.box()
            box.label(text=f"In '{prefs.hub_name}'", icon='PINNED')
            for e in in_hub:
                r = box.row(align=True)
                op = r.operator("bbutils.tabhub_toggle", text="", icon='X', emboss=False)
                op.panel_id = e.panel_id
                live = by_id.get(e.panel_id) or _hub_find(e.panel_id)
                text = _hub_label(live) if live else f"{e.label} (not loaded)"
                if e.space != self.space:
                    text += f"   [{names.get(e.space, e.space)}]"
                r.label(text=text)
                op = r.operator("bbutils.tabhub_move", text="", icon='TRIA_UP')
                op.panel_id, op.direction = e.panel_id, -1
                op = r.operator("bbutils.tabhub_move", text="", icon='TRIA_DOWN')
                op.panel_id, op.direction = e.panel_id, 1

        # ── every tab with its panels and sections ──
        def original_parent(c):
            pid = _hub_pid(c)
            if pid in _hub_orig:
                return _hub_orig[pid]["parent"]
            return getattr(c, "bl_parent_id", "")

        children = {}
        for c in panels:
            children.setdefault(original_parent(c), []).append(c)

        tabs = {}
        for c in children.get("", []):
            tabs.setdefault(_hub_original_category(c), []).append(c)

        needle = self.search.lower().strip()

        def matches(c, cat):
            if not needle:
                return True
            hay = f"{getattr(c, 'bl_label', '')} {cat} {c.__module__}".lower()
            return needle in hay or any(matches(k, cat) for k in children.get(_hub_pid(c), []))

        col = layout.column(align=True)
        for cat in sorted(tabs, key=str.lower):
            tops = [c for c in tabs[cat] if matches(c, cat)]
            if not tops:
                continue
            box = col.box()
            box.label(text=cat, icon='MENU_PANEL')

            def walk(c, depth):
                self._row(box, c, hubbed, depth)
                for k in children.get(_hub_pid(c), []):
                    walk(k, depth + 1)
            for c in tops:
                walk(c, 0)


def _draw_ezphyzi(layout, context):
    """EzPhyzi section, shared by the 3D View and Dope Sheet panels."""
    mp = context.scene.bbutils_ezphyzi_props
    previewing = _ez_state["handle"] is not None
    box = layout.box()
    box.label(text="EzPhyzi", icon='PHYSICS')
    row = box.row()
    row.scale_y = 1.4
    row.operator("bbutils.ezphyzi_from_active",
                 text="EzPhyzi It", icon='FORCE_FORCE')
    _draw_hint(box, "Pose mode: select the bone to throw,",
                    "go to the release frame, click")

    col = box.column(align=True)
    col.prop(mp, "armature")
    if mp.armature and mp.armature.type == 'ARMATURE':
        col.prop_search(mp, "bone_name", mp.armature.pose, "bones")
    row = col.row(align=True)
    row.prop(mp, "release_frame", text="Release")
    row.prop(mp, "duration", text="Frames")

    row = box.row(align=True)
    row.operator("bbutils.ezphyzi_toggle_preview",
                 text="Hide Preview" if previewing else "Show Preview",
                 icon='HIDE_OFF' if previewing else 'HIDE_ON', depress=previewing)
    row.operator("bbutils.ezphyzi_resample", text="", icon='FILE_REFRESH')

    row = box.row()
    row.scale_y = 1.3
    row.operator("bbutils.ezphyzi_play", text="Launch Preview", icon='PLAY')

    # collapsible sections (Blender 4.1+ layout panels; open/closed state is
    # remembered per editor)
    header, body = box.panel("BBUTILS_ez_display", default_closed=True)
    header.label(text="Display", icon='OVERLAY')
    if body:
        body.prop(mp, "show_in_front")
        body.prop(mp, "hide_real_mesh")
        body.prop(mp, "ghost_step")

    header, body = box.panel("BBUTILS_ez_pivot", default_closed=True)
    header.label(text="Pivot (bone local)", icon='PIVOT_CURSOR')
    header.operator("bbutils.ezphyzi_center_pivot", text="", icon='PIVOT_BOUNDBOX')
    if body:
        body.prop(mp, "pivot_offset", text="")

    header, body = box.panel("BBUTILS_ez_physics", default_closed=False)
    header.label(text="Physics", icon='PHYSICS')
    if body:
        col = body.column(align=True)
        col.prop(mp, "gravity_scale", slider=False)
        col.prop(mp, "inherit_velocity")
        col.prop(mp, "inherit_spin")
        col.prop(mp, "linear_drag")
        col.prop(mp, "angular_damping")
        row = body.row(align=True)
        row.prop(mp, "use_floor")
        sub = row.row(align=True)
        sub.active = mp.use_floor
        sub.prop(mp, "floor_height", text="")
        if mp.use_floor:
            row = body.row(align=True)
            row.prop(mp, "bounce", slider=True)
            row.prop(mp, "friction", slider=True)

    header, body = box.panel("BBUTILS_ez_extra", default_closed=True)
    header.label(text="Extra Push & Spin", icon='FORCE_WIND')
    if body:
        body.prop(mp, "extra_impulse")
        body.prop(mp, "extra_spin")

    box.prop(mp, "key_constraints_off")
    box.prop(mp, "key_type")
    row = box.row()
    row.scale_y = 1.4
    row.operator("bbutils.ezphyzi_bake", text="Bake EzPhyzi", icon='CHECKMARK')
    _draw_hint(box, "Keys the bone directly - no rigid body,",
                    "no Child Of, no extra bake needed")


class BBUTILS_PT_dopesheet_panel(Panel):
    bl_label = "BBUtils"
    bl_idname = "BBUTILS_PT_dopesheet_panel"
    bl_space_type = 'DOPESHEET_EDITOR'
    bl_region_type = 'UI'
    bl_category = "BBUtils"

    def draw(self, context):
        layout = self.layout
        layout.operator("bbutils.tabhub_manager", text="Tab Hub", icon='WINDOW')
        layout.separator(factor=0.5)

        box = layout.box()
        box.label(text="Animation Tools", icon='ANIM')

        sub = box.box()
        sub.label(text="Keyframe Management", icon='KEY_HLT')
        sub.operator("bbutils.wipe_animation_data",       text="Wipe Animation Data",       icon='CANCEL')
        sub.operator("bbutils.key_constraint_influences", text="Key Constraint Influences",  icon='CONSTRAINT')
        sub.operator("bbutils.key_matching_channels",     text="Key Matching Channels",      icon='KEYINGSET')

        sub = box.box()
        sub.label(text="Baking", icon='RENDER_ANIMATION')
        sub.operator("bbutils.bake_between_keyframes", text="Bake Between Keyframes", icon='KEY_HLT')

        layout.separator(factor=1.0)
        _draw_ezphyzi(layout, context)
        layout.separator(factor=1.0)

        box = layout.box()
        box.label(text="NLA Management", icon='NLA')
        row = box.row(align=True)
        row.operator("bbutils.stash_all_actions",     text="Stash All",   icon='NLA')
        row.operator("bbutils.delete_all_nla_tracks", text="Delete All",  icon='TRASH')


class BBUTILS_PT_graph_editor_panel(Panel):
    bl_label = "BBUtils"
    bl_idname = "BBUTILS_PT_graph_editor_panel"
    bl_space_type = 'GRAPH_EDITOR'
    bl_region_type = 'UI'
    bl_category = "BBUtils"

    def draw(self, context):
        layout = self.layout
        layout.operator("bbutils.tabhub_manager", text="Tab Hub", icon='WINDOW')
        layout.separator(factor=0.5)

        box = layout.box()
        box.label(text="F-Curve Tools", icon='FCURVE')
        box.operator("bbutils.flip_fcurve", text="Flip F-Curve", icon='ARROW_LEFTRIGHT')
        _draw_hint(box, "Flips selected curves + handles vertically")

        layout.separator(factor=1.0)

        box = layout.box()
        box.label(text="Animation Tools", icon='ANIM')
        box.operator("bbutils.wipe_animation_data",       text="Wipe Animation Data",      icon='CANCEL')
        box.operator("bbutils.key_constraint_influences", text="Key Constraint Influences", icon='CONSTRAINT')


# ─────────────────────────────────────────────────────────────────────────────
#  REGISTRATION
# ─────────────────────────────────────────────────────────────────────────────

class BBUTILS_PT_viewport_panel(Panel):
    bl_label = "BBUtils"
    bl_idname = "BBUTILS_PT_viewport_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "BBUtils"

    def draw(self, context):
        layout = self.layout
        layout.operator("bbutils.tabhub_manager", text="Tab Hub", icon='WINDOW')
        layout.separator(factor=0.5)
        props = context.scene.bbutils_render_props
        ctrl_props = context.scene.bbutils_controller_props

        # ── Constraints & Bones ──────────────────────────────────────────────
        layout.label(text="Constraints & Bones", icon='CONSTRAINT_BONE')
        layout.operator("bbutils.apply_child_of_constraints",
                        text="Apply Child Of Constraints", icon='BONE_DATA')
        layout.operator("bbutils.connect_rigs",
                        text="Connect Rigs", icon='LINKED')
        _draw_hint(layout, "Select 2 rigs; the active (last selected) one is the target",
                           "Copy Transforms on all bones with matching names")
        layout.separator()
        row = layout.row(align=True)
        row.operator("bbutils.create_controller_bones", text="Create Controllers", icon='ADD')
        row.operator("bbutils.remove_controller_bones", text="Remove Controllers", icon='REMOVE')
        layout.prop(ctrl_props, "suffix")
        layout.prop(ctrl_props, "hide_original")
        layout.prop(ctrl_props, "color_controllers")

        layout.separator()
        shape_props = context.scene.bbutils_shape_props
        box = layout.box()
        box.label(text="Bulk Custom Shape", icon='MESH_DATA')
        box.prop(shape_props, "shape_preset")
        if shape_props.shape_preset == 'CUSTOM':
            box.prop(shape_props, "custom_shape_object", text="Object")
        row = box.row(align=True)
        row.operator("bbutils.set_custom_shape",   text="Set on Selected", icon='OUTLINER_OB_MESH')
        row.operator("bbutils.reset_custom_shape", text="Reset",           icon='BONE_DATA')
        _draw_hint(box, "Set: assigns the shape above to ALL selected bones",
                        "Reset: clears shape and/or its transform")

        layout.separator()
        layout.operator("bbutils.unlock_channels", text="Unlock All Channels", icon='UNLOCKED')
        _draw_hint(layout, "Clears Location/Rotation/Scale padlocks on selected bones")

        layout.separator(factor=1.5)

        # ── Animation ────────────────────────────────────────────────────────
        layout.label(text="Animation", icon='ANIM')
        layout.operator("bbutils.wipe_animation_data",
                        text="Wipe Animation Data", icon='CANCEL')
        layout.operator("bbutils.key_constraint_influences",
                        text="Key Constraint Influences", icon='CONSTRAINT')
        layout.operator("bbutils.key_matching_channels",
                        text="Key Matching Channels", icon='KEYINGSET')
        layout.operator("bbutils.bake_between_keyframes",
                        text="Bake Between Keyframes", icon='KEY_HLT')

        layout.separator(factor=1.5)

        # ── EzPhyzi ─────────────────────────────────────────────────────────
        _draw_ezphyzi(layout, context)

        layout.separator(factor=1.5)

        # ── NLA ──────────────────────────────────────────────────────────────
        layout.label(text="NLA Management", icon='NLA')
        row = layout.row(align=True)
        row.operator("bbutils.stash_all_actions",     text="Stash All",    icon='NLA')
        row.operator("bbutils.delete_all_nla_tracks", text="Delete All",   icon='TRASH')

        layout.separator(factor=1.5)

        # ── F-Curves ─────────────────────────────────────────────────────────
        layout.label(text="F-Curve Tools", icon='FCURVE')
        layout.operator("bbutils.flip_fcurve", text="Flip F-Curve", icon='ARROW_LEFTRIGHT')

        layout.separator(factor=1.5)

        # ── Batch Render ─────────────────────────────────────────────────────
        layout.label(text="Batch Render", icon='RENDER_ANIMATION')
        layout.prop(props, "output_folder", text="")
        layout.prop(props, "armature_object", text="Armature")
        layout.prop(props, "camera_object",   text="Camera")
        layout.prop(props, "action_filter",   text="Filter")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "resolution_x", text="X")
        row.prop(props, "resolution_y", text="Y")
        col.prop(props, "resolution_percentage", slider=True)

        layout.prop(props, "use_custom_range")
        if props.use_custom_range:
            row = layout.row(align=True)
            row.prop(props, "frame_start", text="Start")
            row.prop(props, "frame_end",   text="End")

        layout.separator(factor=0.5)
        row = layout.row()
        row.scale_y = 1.6
        row.operator("bbutils.batch_viewport_render",
                     text="Render All Actions", icon='RENDER_ANIMATION')

classes = [
    BBUtilsHubEntry,
    BBUtilsPreferences,
    BBUTILS_OT_tabhub_toggle,
    BBUTILS_OT_tabhub_move,
    BBUTILS_OT_tabhub_manager,
    BBUtilsBatchRenderProperties,
    BBUtilsControllerProperties,
    BBUtilsShapeProperties,
    BBUtilsEzPhyziProperties,
    BBUTILS_OT_apply_child_of_constraints,
    BBUTILS_OT_connect_rigs,
    BBUTILS_OT_create_controller_bones,
    BBUTILS_OT_remove_controller_bones,
    BBUTILS_OT_reset_custom_shape,
    BBUTILS_OT_set_custom_shape,
    BBUTILS_OT_unlock_channels,
    BBUTILS_OT_wipe_animation_data,
    BBUTILS_OT_key_constraint_influences,
    BBUTILS_OT_key_matching_channels,
    BBUTILS_OT_stash_all_actions,
    BBUTILS_OT_delete_all_nla_tracks,
    BBUTILS_OT_bake_between_keyframes,
    BBUTILS_OT_ezphyzi_from_active,
    BBUTILS_OT_ezphyzi_toggle_preview,
    BBUTILS_OT_ezphyzi_play,
    BBUTILS_OT_ezphyzi_center_pivot,
    BBUTILS_OT_ezphyzi_resample,
    BBUTILS_OT_ezphyzi_bake,
    BBUTILS_OT_flip_fcurve,
    BBUTILS_OT_batch_viewport_render,
    BBUTILS_PT_dopesheet_panel,
    BBUTILS_PT_graph_editor_panel,
    BBUTILS_PT_viewport_panel,
]


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.bbutils_render_props = PointerProperty(
        type=BBUtilsBatchRenderProperties)
    bpy.types.Scene.bbutils_controller_props = PointerProperty(
        type=BBUtilsControllerProperties)
    bpy.types.Scene.bbutils_shape_props = PointerProperty(
        type=BBUtilsShapeProperties)
    bpy.types.Scene.bbutils_ezphyzi_props = PointerProperty(
        type=BBUtilsEzPhyziProperties)
    bpy.app.handlers.load_pre.append(_ez_on_load_pre)
    # give other add-ons a moment to register, then keep the hub in sync
    bpy.app.timers.register(_hub_watchdog, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_hub_watchdog):
        bpy.app.timers.unregister(_hub_watchdog)
    _hub_restore_all()
    _ez_preview_off()
    if _ez_on_load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_ez_on_load_pre)
    del bpy.types.Scene.bbutils_ezphyzi_props
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.bbutils_render_props
    del bpy.types.Scene.bbutils_controller_props
    del bpy.types.Scene.bbutils_shape_props


if __name__ == "__main__":
    register()