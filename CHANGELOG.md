# Changelog

## 1.16.0
- **Tab Hub across editors**: a new **Hub In** setting puts the hub tab in a chosen
  editor, for example the Dope Sheet. Panels from other editors' tabs are copied
  there and the originals stay where they were.
- The grey EzPhyzi stand-in mesh stays visible with Overlays / Extras off. Only the
  helper lines and ghosts hide.

## 1.15.0
- The EzPhyzi overlay is now hidden behind scene geometry. A new **In Front** option draws it on top again.
- EzPhyzi settings are grouped into collapsible sections: **Display**, **Pivot**,
  **Physics** and **Extra Push & Spin** (rarely used sections start collapsed).

## 1.14.0
- **Tab Hub**: move whole panels, or single sections, from any add-on into one sidebar
  tab placed at the top of the tab strip. You can reorder the hub and send panels back.
  The setup is saved in the add-on preferences and re-applied automatically.
- A **Tab Hub** button at the top of every BBUtils panel.

## 1.13.0
- Fixed a crash when the real mesh was hidden during frame changes: visibility is now
  changed outside depsgraph evaluation.
- The EzPhyzi overlay follows the viewport **Overlays / Extras** toggle.
- EzPhyzi is also in the Dope Sheet sidebar panel.

## 1.12.0
- **Launch Preview**: a one-shot, real-time launch along the path that never moves the timeline.
- Ghost geometry is found through any hierarchy (bone → empty → mesh), Child Of /
  Copy Transforms constraints and skinned vertex groups.
- Solid grey shaded ghost on the current frame.

## 1.11.0
- Pivot offset (XYZ, bone-local) with a live pivot dot and **Pivot To Mesh Centre**.
- Option to hide the real mesh in flight.
- Keyframe type for baked keys (Jitter by default).

## 1.10.0
- Magazine Drop renamed to **EzPhyzi**.

## 1.9.0
- **Magazine Drop**: ballistic throw simulation off a bone, live trajectory overlay,
  floor collision and a direct bake onto the bone.
- Packaged as a Blender 4.2+ extension (`blender_manifest.toml`, `build.py`).
