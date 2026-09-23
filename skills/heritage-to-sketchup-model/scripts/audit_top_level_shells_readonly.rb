require 'json'
require 'fileutils'

module CodexTopLevelShellAudit
  OUT_DIR = File.join(Dir.home, 'HEBI_CAD_SketchUp', 'reference_shell_audit', 'top_level_compare')

  module_function

  def mm(value)
    (value.to_f * 25.4).round(3)
  end

  def point_mm(point)
    [mm(point.x), mm(point.y), mm(point.z)]
  end

  def bounds_data(bounds)
    min = point_mm(bounds.min)
    max = point_mm(bounds.max)
    {
      'min_mm' => min,
      'max_mm' => max,
      'size_mm' => max.zip(min).map { |hi, lo| (hi - lo).round(3) },
      'center_mm' => point_mm(bounds.center)
    }
  end

  def determinant(transform)
    transform.xaxis.dot(transform.yaxis.cross(transform.zaxis)).round(6)
  end

  def walk(entities, transform, stats)
    entities.each do |entity|
      stats['types'][entity.typename] += 1
      case entity
      when Sketchup::Face
        material = entity.material || entity.back_material
        stats['face_materials'][material ? material.name : '(default)'] += 1
        entity.vertices.each do |vertex|
          z = mm(vertex.position.transform(transform).z).round
          stats['z_vertex_counts_mm'][z] += 1
        end
      when Sketchup::Edge
        entity.vertices.each do |vertex|
          z = mm(vertex.position.transform(transform).z).round
          stats['z_edge_counts_mm'][z] += 1
        end
      when Sketchup::Group
        stats['mirrored_nested_instances'] += 1 if determinant(transform * entity.transformation) < 0
        walk(entity.entities, transform * entity.transformation, stats)
      when Sketchup::ComponentInstance
        stats['component_definitions'][entity.definition.name] += 1
        stats['mirrored_nested_instances'] += 1 if determinant(transform * entity.transformation) < 0
        walk(entity.definition.entities, transform * entity.transformation, stats)
      end
    end
  end

  def shell_stats(entity)
    stats = {
      'types' => Hash.new(0),
      'face_materials' => Hash.new(0),
      'component_definitions' => Hash.new(0),
      'z_vertex_counts_mm' => Hash.new(0),
      'z_edge_counts_mm' => Hash.new(0),
      'mirrored_nested_instances' => 0
    }
    if entity.is_a?(Sketchup::Group)
      walk(entity.entities, entity.transformation, stats)
    elsif entity.is_a?(Sketchup::ComponentInstance)
      walk(entity.definition.entities, entity.transformation, stats)
    end
    stats['dominant_z_vertices_mm'] = stats.delete('z_vertex_counts_mm').sort_by { |z, count| [-count, z] }.first(80).to_h
    stats['dominant_z_edges_mm'] = stats.delete('z_edge_counts_mm').sort_by { |z, count| [-count, z] }.first(80).to_h
    stats['face_materials'] = stats['face_materials'].sort_by { |name, count| [-count, name] }.to_h
    stats['component_definitions'] = stats['component_definitions'].sort_by { |name, count| [-count, name] }.to_h
    stats
  end

  def export_entity_views(model, entity, prefix)
    view = model.active_view
    camera = view.camera
    saved_camera = [camera.eye, camera.target, camera.up, camera.perspective?, camera.fov]
    top_level = model.entities.to_a
    saved_hidden = top_level.map { |item| [item, item.hidden?] }
    top_level.each { |item| item.hidden = item != entity }
    bounds = entity.bounds
    center = bounds.center
    diagonal = [bounds.diagonal.to_f, 1000.mm].max
    views = {
      'top' => [Geom::Point3d.new(center.x, center.y, center.z + diagonal * 2.0), center, Y_AXIS, false],
      'south' => [Geom::Point3d.new(center.x, center.y - diagonal * 2.0, center.z), center, Z_AXIS, false],
      'north' => [Geom::Point3d.new(center.x, center.y + diagonal * 2.0, center.z), center, Z_AXIS, false],
      'east' => [Geom::Point3d.new(center.x + diagonal * 2.0, center.y, center.z), center, Z_AXIS, false],
      'west' => [Geom::Point3d.new(center.x - diagonal * 2.0, center.y, center.z), center, Z_AXIS, false],
      'overview' => [Geom::Point3d.new(center.x - diagonal, center.y - diagonal, center.z + diagonal * 0.75), center, Z_AXIS, true]
    }
    views.each do |name, args|
      view.camera = Sketchup::Camera.new(args[0], args[1], args[2])
      view.camera.perspective = args[3]
      view.zoom_extents
      view.write_image(File.join(OUT_DIR, "#{prefix}_#{name}.png"), 1800, 1200, true, 0.92)
    end
  ensure
    saved_hidden&.each { |item, hidden| item.hidden = hidden if item.valid? }
    if view && saved_camera
      restored = Sketchup::Camera.new(saved_camera[0], saved_camera[1], saved_camera[2])
      restored.perspective = saved_camera[3]
      restored.fov = saved_camera[4] if saved_camera[3]
      view.camera = restored
    end
  end

  def run
    model = Sketchup.active_model
    FileUtils.mkdir_p(OUT_DIR)
    candidates = model.entities.to_a.select { |entity| entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance) }
    rows = []
    candidates.each_with_index do |entity, index|
      raw_name = [entity.name.to_s, entity.is_a?(Sketchup::ComponentInstance) ? entity.definition.name.to_s : ''].join('_')
      safe_name = raw_name.gsub(/[^0-9A-Za-z_-]+/, '_')[0, 60]
      safe_name = "top_level_#{index + 1}" if safe_name.empty?
      prefix = format('%02d_%s', index + 1, safe_name)
      rows << {
        'prefix' => prefix,
        'type' => entity.typename,
        'name' => entity.name.to_s,
        'definition_name' => entity.is_a?(Sketchup::ComponentInstance) ? entity.definition.name.to_s : '',
        'layer' => entity.layer ? entity.layer.name : '',
        'top_level_transform_determinant' => determinant(entity.transformation),
        'top_level_mirrored' => determinant(entity.transformation) < 0,
        'bounds' => bounds_data(entity.bounds),
        'stats' => shell_stats(entity)
      }
      export_entity_views(model, entity, prefix)
    end
    File.write(File.join(OUT_DIR, 'top_level_shell_compare.json'), JSON.pretty_generate(rows), mode: 'w:UTF-8')
    'readonly top-level comparison written'
  end
end

CodexTopLevelShellAudit.run
