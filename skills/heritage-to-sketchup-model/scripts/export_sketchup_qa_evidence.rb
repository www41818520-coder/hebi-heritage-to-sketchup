# frozen_string_literal: true

# Read-only active-SketchUp projection exporter for independent CAD QA.
# Usage from the bridge: HEBIIndependentQA.export('X:/project/work/qa/independent-qa-plan.json', 'qa-model-export-unique-id')

require 'json'
require 'digest'
require 'fileutils'
require 'pathname'
require 'sketchup.rb'

module HEBIIndependentQA
  extend self

  DICTIONARY = 'HEBI_PRODUCTION'
  SCHEMA = 'cad_to_sketchup.sketchup_qa_evidence.2026-08-06'

  def sha256(path)
    Digest::SHA256.file(path).hexdigest
  end

  def resolve(root, value)
    path = value.to_s.tr('/', File::SEPARATOR)
    File.expand_path(Pathname.new(path).absolute? ? path : File.join(root, path))
  end

  def mm_point(point)
    [point.x.to_mm, point.y.to_mm, point.z.to_mm]
  end

  def normalized(values)
    length = Math.sqrt(values.inject(0.0) { |total, value| total + value.to_f**2 })
    raise 'Registration axis cannot be zero length' if length <= 1e-9
    values.map { |value| value.to_f / length }
  end

  def project(point, transform)
    p = mm_point(point)
    origin = transform.fetch('origin').map(&:to_f)
    sx, sy = transform.fetch('source_origin').map(&:to_f)
    x_axis = normalized(transform.fetch('x_axis'))
    y_axis = normalized(transform.fetch('y_axis'))
    scale = transform.fetch('scale').to_f
    delta = p.each_index.map { |index| p[index] - origin[index] }
    x_value = delta.zip(x_axis).inject(0.0) { |total, pair| total + pair[0] * pair[1] }
    y_value = delta.zip(y_axis).inject(0.0) { |total, pair| total + pair[0] * pair[1] }
    [sx + x_value / scale, sy + y_value / scale]
  end

  def walk_edges(entities, transformation, points)
    entities.each do |entity|
      case entity
      when Sketchup::Edge
        points << entity.start.position.transform(transformation)
        points << entity.end.position.transform(transformation)
      when Sketchup::Group
        walk_edges(entity.entities, transformation * entity.transformation, points)
      when Sketchup::ComponentInstance
        walk_edges(entity.definition.entities, transformation * entity.transformation, points)
      end
    end
  end

  def convex_hull(points)
    unique = points.map { |point| [point[0].round(6), point[1].round(6)] }.uniq.sort
    return unique if unique.length < 3
    cross = ->(o, a, b) { (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]) }
    lower = []
    unique.each do |point|
      lower.pop while lower.length >= 2 && cross.call(lower[-2], lower[-1], point) <= 0
      lower << point
    end
    upper = []
    unique.reverse_each do |point|
      upper.pop while upper.length >= 2 && cross.call(upper[-2], upper[-1], point) <= 0
      upper << point
    end
    lower[0...-1] + upper[0...-1]
  end

  def bounds(points)
    {'xmin' => points.map(&:first).min, 'ymin' => points.map(&:last).min,
     'xmax' => points.map(&:first).max, 'ymax' => points.map(&:last).max}
  end

  def semantic_entities(root)
    found = {}
    visit = lambda do |entities, transformation|
      entities.each do |entity|
        next unless entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
        combined = transformation * entity.transformation
        topology_id = entity.get_attribute(DICTIONARY, 'topology_id')
        found[topology_id.to_s] = [entity, combined] if topology_id
        child_entities = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
        visit.call(child_entities, combined)
      end
    end
    visit.call(root.entities, root.transformation)
    found
  end

  def category(kind)
    {'level' => 'envelope', 'wall' => 'wall', 'opening' => 'opening', 'curtain_wall' => 'curtain_wall',
     'sweep' => 'sweep', 'canopy' => 'canopy', 'roof' => 'roof', 'slab' => 'slab'}[kind.to_s] || 'other_model_driving'
  end

  def projected_entity(topology_id, entity, transformation, registration, opening_clear)
    raw = []
    child_entities = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
    walk_edges(child_entities, transformation, raw)
    projected = raw.map { |point| project(point, registration.fetch('transform')) }
    raise "Semantic entity #{topology_id} has no projected edges" if projected.empty?
    outline = convex_hull(projected)
    {'topology_id' => topology_id, 'category' => category(entity.get_attribute(DICTIONARY, 'kind')),
     'persistent_entity_ids' => [entity.persistent_id], 'bounds' => bounds(projected), 'outline' => outline,
     'true_opening' => entity.get_attribute(DICTIONARY, 'kind') != 'opening' || opening_clear.fetch(topology_id, false)}
  end

  def projected_material(material_id, target_ids, semantic, registration)
    raw = []
    persistent_ids = []
    target_ids.each do |target_id|
      entity, transformation = semantic.fetch(target_id)
      persistent_ids << entity.persistent_id
      child_entities = entity.is_a?(Sketchup::Group) ? entity.entities : entity.definition.entities
      walk_edges(child_entities, transformation, raw)
    end
    projected = raw.map { |point| project(point, registration.fetch('transform')) }
    raise "Material #{material_id} has no projected target edges" if projected.empty?
    {'topology_id' => material_id, 'category' => 'material_boundary',
     'persistent_entity_ids' => persistent_ids.uniq, 'bounds' => bounds(projected),
     'outline' => convex_hull(projected), 'true_opening' => true}
  end

  def polygon_area(points)
    points.each_with_index.inject(0.0) do |total, pair|
      point, index = pair
      total + point[0].to_f * points[(index + 1) % points.length][1].to_f - points[(index + 1) % points.length][0].to_f * point[1].to_f
    end / 2.0
  end

  def opening_void_checks(topology, semantic)
    walls = topology.fetch('walls').to_h { |item| [item.fetch('id'), item] }
    levels = topology.fetch('levels').to_h { |item| [item.fetch('id'), item] }
    topology.fetch('openings').to_h do |opening|
      wall = walls.fetch(opening.fetch('host_wall_id'))
      wall_entity, wall_transform = semantic.fetch(wall.fetch('id'))
      dx = wall['end'][0].to_f - wall['start'][0].to_f
      dy = wall['end'][1].to_f - wall['start'][1].to_f
      length = Math.sqrt(dx * dx + dy * dy)
      orientation = polygon_area(levels.fetch(opening.fetch('level_id')).fetch('footprint'))
      inward = orientation >= 0 ? [-dy / length, dx / length] : [dy / length, -dx / length]
      position = opening.fetch('position'); z = opening.fetch('sill_elevation_mm').to_f + opening.fetch('height_mm').to_f / 2.0
      local_origin = Geom::Point3d.new((position[0].to_f - inward[0]).mm, (position[1].to_f - inward[1]).mm, z.mm)
      local_direction = Geom::Vector3d.new(inward[0], inward[1], 0)
      inverse = wall_transform.inverse
      origin = local_origin.transform(inverse); direction = local_direction.transform(inverse)
      maximum = (wall.fetch('thickness_mm').to_f + 2.0).mm
      entities = wall_entity.is_a?(Sketchup::Group) ? wall_entity.entities : wall_entity.definition.entities
      intersections = entities.grep(Sketchup::Face).count do |face|
        point = Geom.intersect_line_plane([origin, direction], face.plane)
        next false unless point
        distance = (point - origin).dot(direction)
        distance >= 0 && distance <= maximum && face.classify_point(point) != Sketchup::Face::PointOutside
      end
      [opening.fetch('id'), intersections.zero?]
    end
  end

  def model_point_from_source(source_point, transform)
    origin = Geom::Point3d.new(*transform.fetch('origin').map { |value| value.to_f.mm })
    source_origin = transform.fetch('source_origin').map(&:to_f)
    scale = transform.fetch('scale').to_f
    x_axis = Geom::Vector3d.new(*normalized(transform.fetch('x_axis')))
    y_axis = Geom::Vector3d.new(*normalized(transform.fetch('y_axis')))
    origin.offset(x_axis, ((source_point[0].to_f - source_origin[0]) * scale).mm)
          .offset(y_axis, ((source_point[1].to_f - source_origin[1]) * scale).mm)
  end

  def export_view_image(model, registration, qa_view, output)
    transform = registration.fetch('transform')
    x_axis = Geom::Vector3d.new(*normalized(transform.fetch('x_axis')))
    y_axis = Geom::Vector3d.new(*normalized(transform.fetch('y_axis')))
    direction = x_axis.cross(y_axis)
    raise 'Registration axes are parallel' if direction.length <= 1e-9
    source_bounds = qa_view.fetch('source_bounds')
    source_width = source_bounds.fetch('xmax').to_f - source_bounds.fetch('xmin').to_f
    source_height = source_bounds.fetch('ymax').to_f - source_bounds.fetch('ymin').to_f
    raise 'QA source bounds are empty' if source_width <= 0 || source_height <= 0
    center = model_point_from_source(
      [(source_bounds.fetch('xmin').to_f + source_bounds.fetch('xmax').to_f) / 2.0,
       (source_bounds.fetch('ymin').to_f + source_bounds.fetch('ymax').to_f) / 2.0],
      transform
    )
    eye = center.offset(direction, 100_000.mm)
    camera = Sketchup::Camera.new(eye, center, y_axis); camera.perspective = false
    camera.height = (source_height * transform.fetch('scale').to_f).mm
    if source_width >= source_height
      image_width = 2400
      image_height = [(2400.0 * source_height / source_width).round, 1].max
    else
      image_height = 2400
      image_width = [(2400.0 * source_width / source_height).round, 1].max
    end
    view = model.active_view; previous = view.camera
    begin
      view.camera = camera
      FileUtils.mkdir_p(File.dirname(output))
      view.write_image(filename: output, width: image_width, height: image_height, antialias: true, compression: 0.95, transparent: false)
    ensure
      view.camera = previous
    end
  end

  def untracked_top_level(model, root)
    model.entities.select do |entity|
      next false if entity == root
      next false unless entity.visible?
      entity.is_a?(Sketchup::Face) || entity.is_a?(Sketchup::Edge) || entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
    end.map(&:persistent_id)
  end

  def export(plan_path, derivation_id)
    raise 'A unique model-export derivation ID is required' if derivation_id.to_s.strip.empty?
    plan_path = File.expand_path(plan_path).dup.force_encoding(Encoding::UTF_8)
    plan = JSON.parse(File.read(plan_path, mode: 'r:BOM|UTF-8'))
    raise 'QA plan is not execution-authorized' unless plan['schema'] == 'cad_to_sketchup.independent_qa_plan.2026-08-06' && plan['execution_allowed'] == true
    project_root = File.expand_path(plan.fetch('project_root')).dup.force_encoding(Encoding::UTF_8)
    raise 'QA plan must stay inside its contracted project root' unless plan_path.start_with?(project_root + File::SEPARATOR)
    qa_derivation = nil
    plan.fetch('upstream').each do |item|
      upstream_path = resolve(project_root, item.fetch('path'))
      raise "QA upstream hash is stale: #{item.fetch('kind')}" unless sha256(upstream_path) == item.fetch('sha256').downcase
      qa_derivation = JSON.parse(File.read(upstream_path, mode: 'r:BOM|UTF-8')) if item.fetch('kind') == 'independent-qa-derivation'
    end
    raise 'QA plan has no independent CAD derivation' unless qa_derivation
    %w[derivation tolerance_mm views outputs].each do |key|
      raise "QA plan drifted from CAD derivation: #{key}" unless plan.fetch(key) == qa_derivation.fetch(key)
    end
    active_path = resolve(project_root, plan.fetch('active_model').fetch('path'))
    model = Sketchup.active_model
    raise 'Open the exact contracted production SKP before QA export' unless File.expand_path(model.path) == active_path
    raise 'Active production SKP has unsaved changes' if model.modified?
    raise 'Active production SKP hash is stale' unless sha256(active_path) == plan.fetch('active_model').fetch('sha256').downcase
    root_name = plan.fetch('active_model').fetch('production_root')
    roots = model.entities.grep(Sketchup::Group).select { |item| item.name == root_name }
    raise 'Exactly one contracted production root is required' unless roots.length == 1
    root = roots.first; semantic = semantic_entities(root)
    topology_ref = plan.fetch('upstream').find { |item| item['kind'] == 'building-topology' }
    topology_path = resolve(project_root, topology_ref.fetch('path'))
    raise 'Building topology hash is stale' unless sha256(topology_path) == topology_ref.fetch('sha256').downcase
    topology = JSON.parse(File.read(topology_path, mode: 'r:BOM|UTF-8'))
    build_ref = plan.fetch('upstream').find { |item| item['kind'] == 'sketchup-build' }
    build_path = resolve(project_root, build_ref.fetch('path'))
    raise 'SketchUp build hash is stale' unless sha256(build_path) == build_ref.fetch('sha256').downcase
    build = JSON.parse(File.read(build_path, mode: 'r:BOM|UTF-8'))
    production_plan_path = resolve(project_root, build.fetch('build_plan').fetch('path'))
    raise 'Production plan hash is stale' unless sha256(production_plan_path) == build.fetch('build_plan').fetch('sha256').downcase
    production_plan = JSON.parse(File.read(production_plan_path, mode: 'r:BOM|UTF-8'))
    material_targets = production_plan.fetch('material_assignments').to_h { |item| [item.fetch('material_id'), item.fetch('target_topology_ids')] }
    registrations = topology.fetch('registrations').to_h { |item| [item.fetch('source_view_id'), item] }
    opening_clear = opening_void_checks(topology, semantic)
    model_dir = resolve(project_root, plan.fetch('outputs').fetch('model_view_dir'))
    evidence_path = resolve(project_root, plan.fetch('outputs').fetch('model_evidence_path'))
    views = plan.fetch('views').map do |qa_view|
      view_id = qa_view.fetch('source_view_id'); registration = registrations.fetch(view_id)
      visible = qa_view.fetch('visible_topology_ids')
      missing = visible.reject { |id| semantic.key?(id) || topology.fetch('materials').any? { |item| item.fetch('id') == id } }
      raise "Visible topology IDs have no production entity: #{missing.join(', ')}" unless missing.empty?
      entities = visible.filter_map do |id|
        if semantic.key?(id)
          entity, transformation = semantic.fetch(id)
          projected_entity(id, entity, transformation, registration, opening_clear)
        elsif material_targets.key?(id)
          projected_material(id, material_targets.fetch(id), semantic, registration)
        end
      end
      image_path = File.join(model_dir, "#{view_id}.jpg")
      export_view_image(model, registration, qa_view, image_path)
      all_points = entities.flat_map { |item| item.fetch('outline') }
      {'source_view_id' => view_id, 'qa_view' => qa_view.fetch('qa_view'), 'role' => qa_view.fetch('role'),
       'model_view' => {'path' => image_path.sub(project_root + File::SEPARATOR, '').tr('\\', '/'), 'sha256' => sha256(image_path)},
       'silhouette' => convex_hull(all_points), 'entities' => entities}
    end
    result = {'schema' => SCHEMA, 'status' => 'exported_read_only',
              'qa_plan' => {'path' => plan_path.sub(project_root + File::SEPARATOR, '').tr('\\', '/'), 'sha256' => sha256(plan_path)},
              'active_model' => {'path' => active_path.sub(project_root + File::SEPARATOR, '').tr('\\', '/'), 'sha256' => sha256(active_path)},
              'derivation' => {'id' => derivation_id, 'agent_role' => 'active_sketchup_qa_export'},
              'production_root' => root_name, 'untracked_entity_ids' => untracked_top_level(model, root), 'views' => views, 'unresolved' => []}
    FileUtils.mkdir_p(File.dirname(evidence_path)); File.write(evidence_path, JSON.pretty_generate(result))
    raise 'QA exporter modified the active model' if model.modified?
    result
  end
end
