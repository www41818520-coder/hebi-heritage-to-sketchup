# frozen_string_literal: true

module HEBISourceOpeningPanels
  module_function

  def build(definition, opening, spec, glass)
    width = opening.fetch('width_mm').to_f
    height = opening.fetch('height_mm').to_f
    rects = spec.fetch('panel_rectangles_mm')
    raise 'Source panes require a glass window' unless opening['type'] == 'window' && spec['panel_type'] == 'glass'
    raise 'Conflicting subdivisions' unless spec.fetch('mullion_ratios').empty? && spec.fetch('transom_ratios').empty?
    raise 'No source panes' if rects.empty?
    rects.each_with_index do |rect, index|
      raise 'Invalid pane coordinates' unless rect.length == 4 && rect.all? { |v| v.is_a?(Numeric) && v.finite? }
      x0, z0, x1, z1 = rect
      raise 'Pane outside window' unless 0 < x0 && x0 < x1 && x1 < width && 0 < z0 && z0 < z1 && z1 < height
      rects.take(index).each do |a, b, c, d|
        raise 'Touching or overlapping panes' if [x1,c].min >= [x0,a].max && [z1,d].min >= [z0,b].max
      end
    end
    depth = spec.fetch('frame_depth_mm').to_f
    inset = spec.fetch('inset_mm').to_f
    raise 'Invalid frame depth/inset' unless depth.finite? && depth > 0 && inset.finite? && inset >= 0
    point = ->(x, z, y = inset) { Geom::Point3d.new((x-width/2).mm, y.mm, z.mm) }
    frame = definition.entities.add_group
    frame.name = 'SOURCE_WINDOW_FRAME'
    frame.entities.add_face([point.call(0,0), point.call(width,0), point.call(width,height), point.call(0,height)])
    rects.each do |x0,z0,x1,z1|
      hole = frame.entities.add_face([point.call(x0,z0), point.call(x1,z0), point.call(x1,z1), point.call(x0,z1)])
      raise 'Pane boundary failed' unless hole
      hole.erase!
    end
    face = frame.entities.grep(Sketchup::Face).max_by(&:area)
    raise 'Frame loops incomplete' unless face && face.loops.length == rects.length + 1
    face.reverse! if face.normal.y < 0
    face.pushpull(depth.mm)
    raise 'Nonmanifold source frame' unless frame.manifold?
    panel_depth = [8.0, depth * 0.2].min
    y = inset + (depth-panel_depth)/2
    rects.each_with_index do |(x0,z0,x1,z1), index|
      pane = definition.entities.add_group
      pane.name = "SOURCE_GLASS_#{index+1}"
      face = pane.entities.add_face([point.call(x0,z0,y),point.call(x1,z0,y),point.call(x1,z1,y),point.call(x0,z1,y)])
      raise 'Glass face failed' unless face
      face.reverse! if face.normal.y < 0
      face.pushpull(panel_depth.mm)
      raise 'Nonmanifold source glass' unless pane.manifold?
      pane.material = glass
    end
    rects.length + 1
  end
end
