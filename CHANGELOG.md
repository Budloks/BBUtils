# Changelog

## 1.29.0
- **Transform Clipboard** (3D Viewport): copy the world transform of the active bone or
  object and paste it onto another, or copy where one bone / object sits relative to
  another and paste that offset back on any frame. Pasting accounts for constraints
  (Child Of with any influence, Copy Transforms...) and keys the result when Auto
  Keying is on.
- **Audio** section (3D Viewport, Dope Sheet): Mute Audio and Scrubbing toggles.

## 1.28.0
- Batch Render keeps the scene's **Metadata burn-in** (Output Properties → Metadata →
  Burn Into Image). The note can use tokens, filled in per video: `{action}` `{rig}`
  `{index}` `{count}` `{start}` `{end}` `{blend}`.
- Fixed Batch Render on Blender 5.x (MP4 output is now picked through the new video
  media type) and your FFmpeg output settings are restored afterwards.

## 1.27.0
- EzPhyzi **Clean Curves** (on by default): the bake keeps only the keys the flight
  needs, as Bezier curves with aligned handles. A plain throw gives 2 location keys per
  axis and a few rotation keys instead of one key per frame. **Tolerance** sets how far
  the curve may drift from the simulation (fraction of each channel's range); bounces get
  their own keys with broken handles.
- EzPhyzi: **End Frame** replaces the "Frames" count. It sits right under Start Frame with
  its own "current frame" button, and moves along when the start frame passes it.
- **Cleaner Tab Hub dialog**: two pages (Collect Panels / Tab Order) instead of one long
  list. Tabs are folded by default and show how many of their panels are in the hub, the
  hub's own contents sit in one box at the top, and built-in panels are labelled "Blender".
- Hints under buttons are dimmed, and the EzPhyzi frame fields form one aligned block.

## 1.26.1
- EzPhyzi: the preview now also finds the mesh when it hangs on a different bone that
  follows the picked one (bone children, Copy Transforms, or a lone Child Of), e.g. a
  control bone `MagBetter` driving a deform bone `Mag` with the magazine and bullets.

## 1.26.0
- **Tab Order**: a new section in the Tab Hub dialog reorders the sidebar tabs of any
  editor (up / down / to the top), e.g. to move BBUtils from the bottom of the
  3D Viewport tab strip to the top. The order is saved in the preferences and
  re-applied on startup.

## 1.25.0
- Every BBUtils category is now a collapsible sub-panel in the 3D Viewport, Dope Sheet
  and Graph Editor, just like panels in the Tab Hub. Blender remembers what you
  collapsed, and you can reorder categories by dragging their handle.
- New **Bone Shapes & Locks** category in the 3D Viewport (Bulk Custom Shape and
  Unlock All Channels, split out of Constraints & Bones).

## 1.24.0
- Tab Hub: panels copied into another editor always show up and stay collapsible.
  When the add-on itself hides a panel (for example it needs an active object), the
  hub shows a short note instead of the panel vanishing.

## 1.23.0
- The EzPhyzi pivot snaps to the mesh centre by default whenever you pick a bone
  (**EzPhyzi It** or the Bone field). You can still edit it afterwards.

## 1.22.0
- Removed **Launch Preview**. Just play the animation instead: during playback the
  overlay switches to the same clean look (path, grey simulated mesh and pivot only).

## 1.21.0
- **Bake EzPhyzi** no longer keys scale. Scale keys are only added on frames where the
  baked scale really differs from the animated one, for example under a scaling parent.

## 1.20.0
- Removed **Hide Real Mesh In Flight**. The real mesh now always stays visible during
  the preview, next to the grey simulated one.

## 1.19.0
- New EzPhyzi defaults: **Gravity 8.0** and **Inherit Spin 0.6**, tuned for snappy
  viewmodel magazine throws. Scenes that already use EzPhyzi keep their values.

## 1.18.0
- The EzPhyzi preview follows rig edits live. Moving the bone by hand on the start
  frame (or the frame before it) updates the throw from the live pose without
  changing the frame, so unkeyed edits survive. Changing keys (auto-key, Dope Sheet,
  Graph Editor) re-samples the animation.

## 1.17.0
- A **Start Frame** field sits right under **EzPhyzi It**, with a button that sets it
  to the current frame. Changing it re-simulates the preview at once and turns the
  preview on if it was hidden.

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
