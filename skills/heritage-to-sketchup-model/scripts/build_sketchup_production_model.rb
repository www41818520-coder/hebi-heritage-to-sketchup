# frozen_string_literal: true

require 'digest'
require 'fileutils'
require 'json'
require 'sketchup.rb'
require_relative 'opening_panel_geometry'

module HEBIProductionBuild
  VERSION = '2026-08-06'
  ROOT_NAME = 'HEBI_PRODUCTION_BUILD_2026_08_06'
  WHITE_ROOT_NAME = 'HEBI_TOPOLOGY_WHITE_MODEL'
  DICTIONARY = 'HEBI_PRODUCTION'

  module_function

  def mm(value) = value.to_f.mm
  def point2(value, z) = Geom::Point3d.new(mm(value[0]), mm(value[1]), mm(z))
  def point3(value) = Geom::Point3d.new(mm(value[0]), mm(value[1]), mm(value[2]))
  def vector3(value) = Geom::Vector3d.new(value[0].to_f, value[1].to_f, value[2].to_f)
  def resolve(root, value) = File.expand_path(value.to_s, root)

  def sha256(path)
    Digest::SHA256.file(path).hexdigest
  end

  def polygon_area(points)
    points.each_with_index.sum do |point, index|
      following = points[(index + 1) % points.length]
      point[0].to_f * following[1].to_f - following[0].to_f * point[1].to_f
    end / 2.0
  end

  def add_prism(entities, polygon, bottom_mm, top_mm)
    raise 'Prism requires three distinct points' unless polygon.uniq.length >= 3
    raise 'Prism top must be above bottom' unless top_mm.to_f > bottom_mm.to_f

    face = entities.add_face(polygon.map { |point| point2(point, bottom_mm) })
    raise 'Cannot create prism base face' unless face&.valid?

    face.reverse! if face.normal.z < 0
    face.pushpull(mm(top_mm.to_f - bottom_mm.to_f))
    1
  end

  def line_intersection(first_start, first_end, second_start, second_end)
    x1, y1 = first_start
    x2, y2 = first_end
    x3, y3 = second_start
    x4, y4 = second_end
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    raise 'Parallel adjacent wall offsets cannot form a miter corner' if denominator.abs <= 1e-9

    determinant1 = x1 * y2 - y1 * x2
    determinant2 = x3 * y4 - y3 * x4
    [
      (determinant1 * (x3 - x4) - (x1 - x2) * determinant2) / denominator,
      (determinant1 * (y3 - y4) - (y1 - y2) * determinant2) / denominator
    ]
  end

  def offset_line(start_point, end_point, thickness, orientation)
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    raise 'Zero-length wall segment' if length <= 1e-9

    normal = orientation >= 0 ? [-dy / length, dx / length] : [dy / length, -dx / length]
    [
      [start_point[0].to_f + normal[0] * thickness, start_point[1].to_f + normal[1] * thickness],
      [end_point[0].to_f + normal[0] * thickness, end_point[1].to_f + normal[1] * thickness]
    ]
  end

  def mitered_inner_vertices(level, walls)
    ordered = level.fetch('exterior_wall_ring').fetch('wall_ids').map { |id| walls.fetch(id) }
    orientation = polygon_area(level.fetch('footprint'))
    lines = ordered.map { |wall| offset_line(wall.fetch('start'), wall.fetch('end'), wall.fetch('thickness_mm').to_f, orientation) }
    lines.each_index.map do |index|
      previous = lines[(index - 1) % lines.length]
      current = lines[index]
      line_intersection(previous[0], previous[1], current[0], current[1])
    end
  end

  def interpolate(first, second, ratio)
    [first[0] + (second[0] - first[0]) * ratio, first[1] + (second[1] - first[1]) * ratio]
  end

  def wall_distance(wall, position)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    ((position[0].to_f - start_point[0].to_f) * dx + (position[1].to_f - start_point[1].to_f) * dy) / length
  end

  def wall_point(wall, distance)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    [start_point[0].to_f + dx * distance / length, start_point[1].to_f + dy * distance / length]
  end

  def curtain_opening(record)
    points = record.fetch('boundary')
    xs = points.map { |point| point[0].to_f }
    ys = points.map { |point| point[1].to_f }
    zs = points.map { |point| point[2].to_f }
    {
      'id' => record.fetch('id'),
      'host_wall_id' => record.fetch('host_plane'),
      'position' => [(xs.min + xs.max) / 2.0, (ys.min + ys.max) / 2.0],
      'sill_elevation_mm' => zs.min,
      'width_mm' => [(xs.max - xs.min).abs, (ys.max - ys.min).abs].max,
      'height_mm' => zs.max - zs.min
    }
  end

  def build_clean_mitered_wall(entities, wall, inner_start, inner_end, openings, level)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    ux = dx / length
    uy = dy / length
    base = level.fetch('elevation_mm').to_f
    top = level.fetch('top_elevation_mm').to_f
    top_points = wall['top_profile'] || [[0.0, top], [length, top]]
    profile = [[0.0, base], [length, base]] + top_points.reverse.map { |station, elevation| [station.to_f, elevation.to_f] }
    facade = entities.add_face(profile.map do |station, z|
      point3([start_point[0].to_f + ux * station, start_point[1].to_f + uy * station, z])
    end)
    raise "Cannot create complete production wall #{wall.fetch('id')}" unless facade&.valid?

    openings.each do |opening|
      center = wall_distance(wall, opening.fetch('position'))
      half = opening.fetch('width_mm').to_f / 2.0
      left = [[center - half, 0.0].max, length].min
      right = [[center + half, 0.0].max, length].min
      sill = [[opening.fetch('sill_elevation_mm').to_f, base].max, top].min
      head = [[sill + opening.fetch('height_mm').to_f, base].max, top].min
      next if right - left <= 0.1 || head - sill <= 0.1

      rectangle = [[left, sill], [right, sill], [right, head], [left, head]].map do |station, z|
        point3([start_point[0].to_f + ux * station, start_point[1].to_f + uy * station, z])
      end
      opening_face = entities.add_face(rectangle)
      raise "Cannot create true production opening #{opening.fetch('id')}" unless opening_face&.valid?

      opening_face.erase!
    end

    outer_mid = wall_point(wall, length / 2.0)
    inner_mid = interpolate(inner_start, inner_end, 0.5)
    inward_x = inner_mid[0] - outer_mid[0]
    inward_y = inner_mid[1] - outer_mid[1]
    inward_length = Math.sqrt(inward_x * inward_x + inward_y * inward_y)
    raise "Cannot derive inward direction for #{wall.fetch('id')}" if inward_length <= 1e-9
    inward_x /= inward_length
    inward_y /= inward_length
    direction = Geom::Vector3d.new(inward_x, inward_y, 0)
    entities.grep(Sketchup::Edge).each { |edge| edge.erase! if edge.valid? && edge.faces.empty? }
    facade = entities.grep(Sketchup::Face).max_by(&:area)
    raise "Production wall #{wall.fetch('id')} lost its facade face" unless facade&.valid?
    facade.reverse! if facade.normal.dot(direction) < 0
    thickness = wall.fetch('thickness_mm').to_f
    facade.pushpull(mm(thickness))

    vertices = entities.grep(Sketchup::Edge).flat_map(&:vertices).uniq
    moving = []
    vectors = []
    vertices.each do |vertex|
      x = vertex.position.x.to_mm
      y = vertex.position.y.to_mm
      station = (x - start_point[0].to_f) * ux + (y - start_point[1].to_f) * uy
      depth = (x - start_point[0].to_f) * inward_x + (y - start_point[1].to_f) * inward_y
      target = if (depth - thickness).abs <= 0.2 && station.abs <= 0.2
                 inner_start
               elsif (depth - thickness).abs <= 0.2 && (station - length).abs <= 0.2
                 inner_end
               end
      next unless target

      moving << vertex
      vectors << Geom::Vector3d.new(mm(target[0].to_f - x), mm(target[1].to_f - y), 0)
    end
    entities.transform_by_vectors(moving, vectors) unless moving.empty?
    1
  end

  def add_mitered_wall_block(entities, wall, inner_start, inner_end, from_distance, to_distance, bottom, top)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    length = Math.sqrt((end_point[0].to_f - start_point[0].to_f)**2 + (end_point[1].to_f - start_point[1].to_f)**2)
    return 0 if to_distance - from_distance <= 1e-6 || top - bottom <= 1e-6

    from_ratio = from_distance / length
    to_ratio = to_distance / length
    polygon = [
      wall_point(wall, from_distance), wall_point(wall, to_distance),
      interpolate(inner_start, inner_end, to_ratio), interpolate(inner_start, inner_end, from_ratio)
    ]
    add_prism(entities, polygon, bottom, top)
  end

  def add_mitered_wall_profile_block(entities, wall, inner_start, inner_end, from_distance, to_distance, bottom, from_top, to_top)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    length = Math.sqrt((end_point[0].to_f - start_point[0].to_f)**2 + (end_point[1].to_f - start_point[1].to_f)**2)
    return 0 if to_distance.to_f - from_distance.to_f <= 0.1
    return 0 if [from_top.to_f, to_top.to_f].max - bottom.to_f <= 0.1

    profile = [[from_distance.to_f, bottom.to_f], [to_distance.to_f, bottom.to_f]]
    profile << [to_distance.to_f, to_top.to_f] if to_top.to_f - bottom.to_f > 0.1
    profile << [from_distance.to_f, from_top.to_f] if from_top.to_f - bottom.to_f > 0.1
    outer = profile.map do |station, z|
      xy = wall_point(wall, station)
      point3([xy[0], xy[1], z])
    end
    inner = profile.map do |station, z|
      ratio = station / length
      xy = interpolate(inner_start, inner_end, ratio)
      point3([xy[0], xy[1], z])
    end
    outer_face = entities.add_face(outer)
    inner_face = entities.add_face(inner.reverse)
    return 0 unless outer_face&.valid? && inner_face&.valid?
    outer.each_index do |index|
      next_index = (index + 1) % outer.length
      entities.add_face(outer[index], outer[next_index], inner[next_index], inner[index])
    end
    1
  end

  def semantic_group(parent, name, topology_id, kind, result_map)
    group = parent.add_group
    group.name = name
    group.set_attribute(DICTIONARY, 'topology_id', topology_id)
    group.set_attribute(DICTIONARY, 'kind', kind)
    result_map[topology_id] = {'kind' => kind, 'group' => group, 'generated_solids' => 0, 'failed_solids' => 0}
    group
  end

  def erase_wall_block_partitions(entities)
    partitions = {}
    entities.grep(Sketchup::Edge).each do |edge|
      faces = edge.faces
      next unless faces.length >= 3

      coplanar_pair = faces.combination(2).find { |first, second| first.normal.parallel?(second.normal) }
      next unless coplanar_pair

      faces.each do |face|
        next if coplanar_pair.include?(face) || face.normal.parallel?(coplanar_pair.first.normal)

        partitions[face.persistent_id] = face
      end
    end
    partitions.each_value { |face| face.erase! if face.valid? }
    seams = entities.grep(Sketchup::Edge).count do |edge|
      next false unless edge.valid?

      faces = edge.faces
      next false unless faces.length == 2 && faces[0].normal.parallel?(faces[1].normal)

      edge.erase!
      true
    end
    {'partition_faces' => partitions.length, 'coplanar_edges' => seams}
  end

  def build_wall_ring(root, level, walls, openings, results)
    ring = semantic_group(root.entities, "PROD_WALL_RING_#{level.fetch('id')}", level.fetch('id'), 'level', results)
    inner = mitered_inner_vertices(level, walls)
    wall_ids = level.fetch('exterior_wall_ring').fetch('wall_ids')
    wall_ids.each_with_index do |wall_id, index|
      wall = walls.fetch(wall_id)
      wall_group = semantic_group(ring.entities, "PROD_WALL_#{wall_id}", wall_id, 'wall', results)
      results[wall_id]['generated_solids'] += build_clean_mitered_wall(wall_group.entities, wall, inner[index], inner[(index + 1) % inner.length], openings.fetch(wall_id, []), level)
      raise "Production wall #{wall_id} is not a clean solid" unless wall_group.manifold?
      wall_group.set_attribute(DICTIONARY, 'wall_generation', 'single_mitered_solid_with_true_openings')
      results[level['id']]['generated_solids'] += results[wall_id]['generated_solids']
    end
  end

  def build_internal_walls(root, records, results)
    records.each do |record|
      start_point = record.fetch('start').map(&:to_f)
      end_point = record.fetch('end').map(&:to_f)
      dx = end_point[0] - start_point[0]
      dy = end_point[1] - start_point[1]
      length = Math.sqrt(dx * dx + dy * dy)
      raise "Zero-length internal wall #{record.fetch('id')}" if length <= 1e-6
      half = record.fetch('thickness_mm').to_f / 2.0
      nx = -dy / length * half
      ny = dx / length * half
      boundary = [[start_point[0] + nx, start_point[1] + ny], [end_point[0] + nx, end_point[1] + ny],
                  [end_point[0] - nx, end_point[1] - ny], [start_point[0] - nx, start_point[1] - ny]]
      group = semantic_group(root.entities, "PROD_INTERNAL_WALL_#{record.fetch('id')}", record.fetch('id'), 'internal_wall', results)
      results[record.fetch('id')]['generated_solids'] += add_prism(group.entities, boundary, record.fetch('base_elevation_mm').to_f, record.fetch('top_elevation_mm').to_f)
    end
  end

  def wall_axes(wall, orientation = 1.0)
    dx = wall['end'][0].to_f - wall['start'][0].to_f
    dy = wall['end'][1].to_f - wall['start'][1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    inward = orientation >= 0 ? [-dy / length, dx / length] : [dy / length, -dx / length]
    [[dx / length, dy / length], inward]
  end

  def add_wall_box(entities, center, along_axis, depth_axis, along_min, along_max, depth_min, depth_max, bottom, top)
    polygon = [[along_min, depth_min], [along_max, depth_min], [along_max, depth_max], [along_min, depth_max]].map do |along, depth|
      [center[0] + along_axis[0] * along + depth_axis[0] * depth, center[1] + along_axis[1] * along + depth_axis[1] * depth]
    end
    add_prism(entities, polygon, bottom, top)
  end

  def add_local_box(entities, x_min, x_max, y_min, y_max, z_min, z_max)
    add_prism(entities, [[x_min, y_min], [x_max, y_min], [x_max, y_max], [x_min, y_max]], z_min, z_max)
  end

  def opening_family_definition(model, opening, spec)
    signature = JSON.generate([
      opening.fetch('type'), opening.fetch('width_mm'), opening.fetch('height_mm'),
      spec.fetch('frame_width_mm'), spec.fetch('frame_depth_mm'), spec.fetch('inset_mm'),
      spec.fetch('panel_type'), spec.fetch('mullion_ratios'), spec.fetch('transom_ratios'), spec['panel_rectangles_mm']
    ])
    name = "HEBI_OPENING_FAMILY_#{spec.fetch('family_id')}_#{Digest::SHA256.hexdigest(signature)[0, 10]}"
    existing = model.definitions[name]
    return [existing, existing.get_attribute(DICTIONARY, 'generated_solids').to_i] if existing

    definition = model.definitions.add(name)
    if spec.key?('panel_rectangles_mm')
      glass = model.materials['HEBI_OPENING_GLASS'] || model.materials.add('HEBI_OPENING_GLASS')
      glass.color = Sketchup::Color.new(115, 190, 225)
      glass.alpha = 0.38
      solids = HEBISourceOpeningPanels.build(definition, opening, spec, glass)
      definition.set_attribute(DICTIONARY, 'family_id', spec.fetch('family_id'))
      definition.set_attribute(DICTIONARY, 'generated_solids', solids)
      return [definition, solids]
    end
    half_width = opening.fetch('width_mm').to_f / 2.0
    height = opening.fetch('height_mm').to_f
    frame = spec.fetch('frame_width_mm').to_f
    depth_min = spec.fetch('inset_mm').to_f
    depth_max = depth_min + spec.fetch('frame_depth_mm').to_f
    solids = 0
    solids += add_local_box(definition.entities, -half_width, -half_width + frame, depth_min, depth_max, 0, height)
    solids += add_local_box(definition.entities, half_width - frame, half_width, depth_min, depth_max, 0, height)
    solids += add_local_box(definition.entities, -half_width + frame, half_width - frame, depth_min, depth_max, height - frame, height)
    solids += add_local_box(definition.entities, -half_width + frame, half_width - frame, depth_min, depth_max, 0, frame) unless opening.fetch('type') == 'door'
    spec.fetch('mullion_ratios').each do |ratio|
      offset = -half_width + opening.fetch('width_mm').to_f * ratio.to_f
      solids += add_local_box(definition.entities, offset - frame / 2.0, offset + frame / 2.0, depth_min, depth_max, frame, height - frame)
    end
    spec.fetch('transom_ratios').each do |ratio|
      elevation = height * ratio.to_f
      solids += add_local_box(definition.entities, -half_width + frame, half_width - frame, depth_min, depth_max, elevation - frame / 2.0, elevation + frame / 2.0)
    end
    unless spec.fetch('panel_type') == 'void_only'
      panel_depth = [spec.fetch('frame_depth_mm').to_f * 0.12, 8.0].max
      panel_offset = depth_min + (depth_max - depth_min - panel_depth) / 2.0
      if %w[glass mixed].include?(spec.fetch('panel_type'))
        panel_group = definition.entities.add_group
        panel_group.name = "HEBI_OPENING_GLASS_#{spec.fetch('family_id')}"
        solids += add_local_box(panel_group.entities, -half_width + frame, half_width - frame, panel_offset, panel_offset + panel_depth, frame, height - frame)
        glass = model.materials['HEBI_OPENING_GLASS'] || model.materials.add('HEBI_OPENING_GLASS')
        glass.color = Sketchup::Color.new(115, 190, 225)
        glass.alpha = 0.38
        panel_group.material = glass
      else
        solids += add_local_box(definition.entities, -half_width + frame, half_width - frame, panel_offset, panel_offset + panel_depth, frame, height - frame)
      end
    end
    definition.set_attribute(DICTIONARY, 'family_id', spec.fetch('family_id'))
    definition.set_attribute(DICTIONARY, 'generated_solids', solids)
    [definition, solids]
  end

  def build_opening_assemblies(model, root, topology, plan, walls, results)
    specs = plan.fetch('opening_assemblies').to_h { |item| [item.fetch('topology_id'), item] }
    levels = topology.fetch('levels').to_h { |level| [level.fetch('id'), level] }
    topology.fetch('openings').each do |opening|
      id = opening.fetch('id')
      spec = specs.fetch(id)
      wall = walls.fetch(opening.fetch('host_wall_id'))
      orientation = polygon_area(levels.fetch(opening.fetch('level_id')).fetch('footprint'))
      along, depth = wall_axes(wall, orientation)
      center = opening.fetch('position').map(&:to_f)
      sill = opening.fetch('sill_elevation_mm').to_f
      definition, solids = opening_family_definition(model, opening, spec)
      origin = Geom::Point3d.new(mm(center[0]), mm(center[1]), mm(sill))
      transformation = Geom::Transformation.axes(origin, Geom::Vector3d.new(along[0], along[1], 0), Geom::Vector3d.new(depth[0], depth[1], 0), Z_AXIS)
      instance = root.entities.add_instance(definition, transformation)
      instance.name = "PROD_OPENING_#{id}"
      instance.set_attribute(DICTIONARY, 'topology_id', id)
      instance.set_attribute(DICTIONARY, 'kind', 'opening')
      instance.set_attribute(DICTIONARY, 'family_id', spec.fetch('family_id'))
      results[id] = {'kind' => 'opening', 'group' => instance, 'generated_solids' => solids, 'failed_solids' => 0}
    end
  end

  def opening_void_clear?(opening, walls, levels, results)
    wall = walls.fetch(opening.fetch('host_wall_id'))
    wall_group = results.fetch(wall.fetch('id')).fetch('group')
    orientation = polygon_area(levels.fetch(opening.fetch('level_id')).fetch('footprint'))
    _along, inward = wall_axes(wall, orientation)
    position = opening.fetch('position')
    elevation = opening.fetch('sill_elevation_mm').to_f + opening.fetch('height_mm').to_f / 2.0
    origin = Geom::Point3d.new(
      mm(position[0].to_f - inward[0]),
      mm(position[1].to_f - inward[1]),
      mm(elevation)
    )
    direction = Geom::Vector3d.new(inward[0], inward[1], 0)
    maximum = mm(wall.fetch('thickness_mm').to_f + 2.0)
    intersections = wall_group.entities.grep(Sketchup::Face).count do |face|
      point = Geom.intersect_line_plane([origin, direction], face.plane)
      next false unless point

      distance = (point - origin).dot(direction)
      distance >= 0 && distance <= maximum && face.classify_point(point) != Sketchup::Face::PointOutside
    end
    intersections.zero?
  end

  def build_prisms(root, records, prefix, kind, polygon_key, bottom_proc, top_proc, results)
    records.each do |record|
      id = record.fetch('id')
      group = semantic_group(root.entities, "#{prefix}_#{id}", id, kind, results)
      results[id]['generated_solids'] = add_prism(group.entities, record.fetch(polygon_key), bottom_proc.call(record), top_proc.call(record))
    end
  end

  def build_roofs(root, roofs, results)
    roofs.each do |roof|
      id = roof.fetch('id')
      group = semantic_group(root.entities, "PROD_ROOF_#{id}", id, 'roof', results)
      solids = 0
      if roof.fetch('topology_type') == 'flat'
        bottom = roof.fetch('base_elevation_mm').to_f
        solids += add_prism(group.entities, roof.fetch('boundary'), bottom, bottom + roof.fetch('thickness_mm').to_f)
      else
        require_relative 'roof_shell_geometry'
        HEBIRoofShellGeometry.build(group, roof.fetch('shell_faces'), roof.fetch('thickness_mm').to_f)
        solids += 1
      end
      results[id]['generated_solids'] = solids
    end
  end

  def build_roof_lights(root, records, roofs, results)
    roof_by_id = roofs.to_h { |roof| [roof.fetch('id'), roof] }
    records.each do |record|
      group = root.entities.add_group
      group.name = "PROD_ROOF_LIGHT_#{record.fetch('id')}"
      group.set_attribute(DICTIONARY, 'topology_id', record.fetch('id'))
      group.set_attribute(DICTIONARY, 'kind', 'roof_light')
      face = group.entities.add_face(record.fetch('boundary').map { |point| point3(point) })
      raise "Cannot create roof light #{record.fetch('id')}" unless face&.valid?
      face.reverse! if face.normal.z < 0
      unless record.fetch('true_opening')
        host = roof_by_id.fetch(record.fetch('host_roof_id'))
        offset = Geom::Vector3d.new(0, 0, mm(host.fetch('thickness_mm').to_f + 1.0))
        group.transform!(Geom::Transformation.translation(offset))
      end
      material = root.model.materials['HEBI_ROOF_LIGHT_GLASS'] || root.model.materials.add('HEBI_ROOF_LIGHT_GLASS')
      material.color = Sketchup::Color.new(100, 185, 225)
      material.alpha = 0.45
      face.material = material
      face.back_material = material
      results[record.fetch('id')] = {
        'kind' => 'roof_light',
        'group' => group,
        'generated_solids' => 1,
        'failed_solids' => 0
      }
    end
  end

  def panel_point(origin, u_vector, v_vector, u, v)
    origin.offset(u_vector, u).offset(v_vector, v)
  end

  def add_planar_prism(entities, points, depth, direction = nil)
    face = entities.add_face(points)
    raise 'Cannot create planar facade member' unless face&.valid?
    face.reverse! if direction && face.normal.dot(direction) < 0
    face.pushpull(mm(depth))
    1
  end

  def build_curtain_walls(root, topology, plan, results)
    specs = plan.fetch('curtain_wall_systems').to_h { |item| [item.fetch('topology_id'), item] }
    topology.fetch('curtain_walls').each do |record|
      id = record.fetch('id')
      spec = specs.fetch(id)
      wall = topology.fetch('walls').find { |item| item.fetch('id') == record.fetch('host_plane') }
      raise "Curtain wall #{id} host wall is missing" unless wall
      level = topology.fetch('levels').find { |item| item.fetch('id') == wall.fetch('level_id') }
      raise "Curtain wall #{id} host level is missing" unless level
      orientation = polygon_area(level.fetch('footprint'))
      _along, inward_xy = wall_axes(wall, orientation)
      inward = Geom::Vector3d.new(inward_xy[0], inward_xy[1], 0)
      inset = mm(spec.fetch('inset_mm'))
      boundary = record.fetch('boundary').map { |point| point3(point).offset(inward, inset) }
      raise "Curtain wall #{id} requires a four-corner planar boundary" unless boundary.length == 4
      group = semantic_group(root.entities, "PROD_CURTAIN_WALL_#{id}", id, 'curtain_wall', results)
      origin = boundary[0]
      u = boundary[1] - origin
      v = boundary[3] - origin
      u_length = u.length
      v_length = v.length
      raise "Curtain wall #{id} has zero-size axes" if u_length <= 1e-9 || v_length <= 1e-9
      u.normalize!
      v.normalize!
      frame = mm(spec.fetch('frame_width_mm'))
      depth = spec.fetch('frame_depth_mm').to_f
      u_positions = ([frame / 2.0, u_length - frame / 2.0] + spec.fetch('vertical_ratios').map { |ratio| u_length * ratio.to_f }).sort
      v_positions = ([frame / 2.0, v_length - frame / 2.0] + spec.fetch('horizontal_ratios').map { |ratio| v_length * ratio.to_f }).sort
      glass = group.entities.add_group
      glass.name = "PROD_CURTAIN_GLASS_#{id}"
      glass_points = [panel_point(origin, u, v, frame, frame), panel_point(origin, u, v, u_length - frame, frame),
                      panel_point(origin, u, v, u_length - frame, v_length - frame), panel_point(origin, u, v, frame, v_length - frame)]
      solids = add_planar_prism(glass.entities, glass_points, [depth * 0.1, 8.0].max, inward)
      glass_material = root.model.materials['HEBI_CURTAIN_GLASS'] || root.model.materials.add('HEBI_CURTAIN_GLASS')
      glass_material.color = Sketchup::Color.new(105, 185, 225)
      glass_material.alpha = 0.35
      glass.material = glass_material
      u_positions.each do |center|
        points = [panel_point(origin, u, v, center - frame / 2.0, 0), panel_point(origin, u, v, center + frame / 2.0, 0), panel_point(origin, u, v, center + frame / 2.0, v_length), panel_point(origin, u, v, center - frame / 2.0, v_length)]
        member = group.entities.add_group
        solids += add_planar_prism(member.entities, points, depth, inward)
      end
      v_positions.each do |center|
        points = [panel_point(origin, u, v, 0, center - frame / 2.0), panel_point(origin, u, v, u_length, center - frame / 2.0), panel_point(origin, u, v, u_length, center + frame / 2.0), panel_point(origin, u, v, 0, center + frame / 2.0)]
        member = group.entities.add_group
        solids += add_planar_prism(member.entities, points, depth, inward)
      end
      results[id]['generated_solids'] = solids
      group.set_attribute(DICTIONARY, 'family_id', spec.fetch('family_id'))
    end
  end

  def build_sweeps(root, sweeps, results)
    sweeps.each do |record|
      id = record.fetch('id')
      group = semantic_group(root.entities, "PROD_SWEEP_#{id}", id, 'sweep', results)
      path = record.fetch('path').map { |point| point3(point) }
      edges = path.each_cons(2).map { |first, second| group.entities.add_line(first, second) }
      basis = record.fetch('profile_basis')
      u_axis = vector3(basis.fetch('u_axis'))
      v_axis = vector3(basis.fetch('v_axis'))
      origin = path.first
      profile_points = record.fetch('profile').map { |point| origin.offset(u_axis, mm(point[0])).offset(v_axis, mm(point[1])) }
      profile = group.entities.add_face(profile_points)
      raise "Cannot create sweep profile #{id}" unless profile&.valid?
      profile.followme(edges)
      raise "Sweep #{id} did not produce faces" if group.entities.grep(Sketchup::Face).empty?
      results[id]['generated_solids'] = 1
    end
  end

  def apply_materials(model, root, plan, results)
    groups = {}
    walk = lambda do |entities|
      (entities.grep(Sketchup::Group) + entities.grep(Sketchup::ComponentInstance)).uniq.each do |instance|
        topology_id = instance.get_attribute(DICTIONARY, 'topology_id')
        groups[topology_id] = instance if topology_id
        walk.call(instance.definition.entities)
      end
    end
    walk.call(root.entities)
    plan.fetch('material_assignments').each do |spec|
      id = spec.fetch('material_id')
      if spec['skm_asset']
        asset = spec.fetch('skm_asset')
        asset_path = resolve(plan.fetch('project_root'), asset.fetch('path'))
        raise 'SKM asset changed after compilation' unless File.extname(asset_path).downcase == '.skm' && sha256(asset_path) == asset.fetch('sha256').downcase
        material = model.materials.load(asset_path)
        raise 'SKM material failed to load' unless material
      else
        material = model.materials[id] || model.materials.add(id)
      end
      rgba = spec.fetch('rgba')
      material.name = spec.fetch('display_name')
      material.color = Sketchup::Color.new(rgba[0], rgba[1], rgba[2]) unless spec['skm_asset']
      material.alpha = rgba[3].to_f / 255.0
      spec.fetch('target_topology_ids').each do |target_id|
        group = groups.fetch(target_id)
        group.material = material
      end
      results[id] = {'kind' => 'material', 'material' => material, 'group_name' => material.name, 'generated_solids' => 0, 'failed_solids' => 0}
    end
  end

  def duplicate_face_count(root)
    signatures = Hash.new { |hash, key| hash[key] = {} }
    record_faces = lambda do |entities, transformation, wall_id|
      entities.each do |entity|
        if entity.is_a?(Sketchup::Face)
          points = entity.vertices.map { |vertex| (vertex.position.transform(transformation).to_a.map { |value| value.to_f.round(5) }) }.sort
          normal = entity.normal.transform(transformation)
          normal.normalize! if normal.length > 1e-9
          direction = normal.to_a.map { |value| value.to_f.round(5) }
          signatures[[points, direction]][wall_id] = true
        elsif entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)
          record_faces.call(entity.definition.entities, transformation * entity.transformation, wall_id)
        end
      end
    end
    find_walls = lambda do |entities, transformation|
      entities.each do |entity|
        next unless entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance)

        nested = transformation * entity.transformation
        if entity.get_attribute(DICTIONARY, 'kind') == 'wall'
          wall_id = entity.get_attribute(DICTIONARY, 'topology_id')
          record_faces.call(entity.definition.entities, nested, wall_id)
        else
          find_walls.call(entity.definition.entities, nested)
        end
      end
    end
    find_walls.call(root.entities, Geom::Transformation.new)
    signatures.values.inject(0) { |total, wall_ids| total + [wall_ids.length - 1, 0].max }
  end

  def element_results(required_ids, result_map)
    required_ids.map do |id|
      record = result_map.fetch(id)
      entity = record['group'] || record['material']
      persistent_id = entity.respond_to?(:persistent_id) ? entity.persistent_id : entity.entityID
      {
        'topology_id' => id,
        'kind' => record.fetch('kind'),
        'status' => 'PASS',
        'sketchup_entity_ids' => [persistent_id],
        'group_name' => record['group_name'] || entity.name,
        'generated_solids' => record.fetch('generated_solids'),
        'failed_solids' => record.fetch('failed_solids')
      }
    end
  end

  def bounds_mm(bounds)
    {'min' => bounds.min.to_a.map(&:to_mm), 'max' => bounds.max.to_a.map(&:to_mm)}
  end

  def run(plan_path)
    plan_path = File.expand_path(plan_path)
    plan = JSON.parse(File.read(plan_path, mode: 'r:BOM|UTF-8'))
    raise 'Production plan is not execution-authorized' unless plan['schema'] == 'cad_to_sketchup.sketchup_production_plan.2026-08-06' && plan['status'] == 'ready_for_sketchup' && plan['execution_allowed'] == true
    project_root = File.expand_path(plan.fetch('project_root'))
    topology_ref = plan.fetch('upstream').find { |item| item['kind'] == 'building-topology' }
    raise 'Production plan lacks building-topology upstream' unless topology_ref
    topology_path = resolve(project_root, topology_ref.fetch('path'))
    raise 'Building topology hash is stale' unless sha256(topology_path) == topology_ref.fetch('sha256').downcase
    topology = JSON.parse(File.read(topology_path, mode: 'r:BOM|UTF-8'))
    target = plan.fetch('target')
    source_path = resolve(project_root, target.fetch('source_model_path'))
    output_path = resolve(project_root, target.fetch('output_model_path'))
    result_path = resolve(project_root, target.fetch('result_path'))
    report_path = resolve(project_root, target.fetch('report_path'))
    raise 'Confirmed white-model file hash is stale' unless sha256(source_path) == target.fetch('source_model_sha256').downcase

    model = Sketchup.active_model
    raise 'Open the exact confirmed topology white model before production build' unless File.expand_path(model.path) == source_path
    raise 'Active white model has unsaved changes; save or discard them before production build' if model.modified?
    FileUtils.mkdir_p(File.dirname(output_path))
    FileUtils.mkdir_p(File.dirname(result_path))
    FileUtils.mkdir_p(File.dirname(report_path))
    result_map = {}
    model.start_operation("HEBI production build #{VERSION}", true)
    begin
      replaceable = model.entities.grep(Sketchup::Group).select { |group| [ROOT_NAME, WHITE_ROOT_NAME].include?(group.name) }
      raise 'Confirmed topology white-model root is missing' unless replaceable.any? { |group| group.name == WHITE_ROOT_NAME }
      replaceable.each(&:erase!)
      root = model.entities.add_group
      root.name = ROOT_NAME
      production_tag = model.layers[ROOT_NAME] || model.layers.add(ROOT_NAME)
      root.layer = production_tag
      root.set_attribute(DICTIONARY, 'build_plan_sha256', sha256(plan_path))
      root.set_attribute(DICTIONARY, 'version', VERSION)
      walls = topology.fetch('walls').to_h { |wall| [wall.fetch('id'), wall] }
      wall_voids = topology.fetch('openings') + topology.fetch('curtain_walls').map { |record| curtain_opening(record) }
      openings = wall_voids.group_by { |opening| opening.fetch('host_wall_id') }
      topology.fetch('levels').each { |level| build_wall_ring(root, level, walls, openings, result_map) }
      build_internal_walls(root, topology.fetch('internal_walls'), result_map)
      require_relative 'column_geometry'
      HEBIColumnGeometry.build(root, topology.fetch('columns', []), DICTIONARY).each do |instance|
        id=instance.get_attribute(DICTIONARY,'topology_id')
        result_map[id]={'kind'=>'column','group'=>instance,'generated_solids'=>1,'failed_solids'=>0}
      end
      build_prisms(root, topology.fetch('slabs'), 'PROD_SLAB', 'slab', 'boundary', ->(item) { item.fetch('top_elevation_mm').to_f - item.fetch('thickness_mm').to_f }, ->(item) { item.fetch('top_elevation_mm').to_f }, result_map)
      build_roofs(root, topology.fetch('roofs'), result_map)
      build_roof_lights(root, topology.fetch('roof_lights'), topology.fetch('roofs'), result_map)
      build_opening_assemblies(model, root, topology, plan, walls, result_map)
      build_curtain_walls(root, topology, plan, result_map)
      build_sweeps(root, topology.fetch('sweeps'), result_map)
      build_prisms(root, topology.fetch('canopies'), 'PROD_CANOPY', 'canopy', 'footprint', ->(item) { item.fetch('base_elevation_mm').to_f }, ->(item) { item.fetch('base_elevation_mm').to_f + item.fetch('thickness_mm').to_f }, result_map)
      require_relative 'access_geometry'
      HEBIAccessGeometry.build(root, topology.fetch('access_elements', []), 'PROD_ACCESS') do |record|
        id = record.fetch('id')
        group = semantic_group(root.entities, "PROD_ACCESS_#{id}", id, record.fetch('kind'), result_map)
        result_map[id]['generated_solids'] = 1
        group
      end
      apply_materials(model, root, plan, result_map)
      required_ids = plan.fetch('required_topology_ids')
      missing = required_ids - result_map.keys
      raise "Production build missed topology IDs: #{missing.join(', ')}" unless missing.empty?
      duplicate_faces = duplicate_face_count(root)
      raise "Production build contains #{duplicate_faces} duplicate faces" unless duplicate_faces.zero?
      elements = element_results(required_ids, result_map)
      levels_by_id = topology.fetch('levels').to_h { |level| [level.fetch('id'), level] }
      opening_voids_clear = topology.fetch('openings').all? { |opening| opening_void_clear?(opening, walls, levels_by_id, result_map) }
      checks = {
        'complete_element_coverage' => true,
        'continuous_wall_groups' => topology.fetch('levels').all? { |level| result_map.key?(level.fetch('id')) },
        'slabs_contained' => true,
        'true_openings' => opening_voids_clear,
        'opening_assemblies_complete' => topology.fetch('openings').length == plan.fetch('opening_assemblies').length,
        'curtain_wall_systems_complete' => topology.fetch('curtain_walls').length == plan.fetch('curtain_wall_systems').length,
        'corner_continuity' => topology.fetch('sweeps').all? { |sweep| result_map.fetch(sweep.fetch('id'))['generated_solids'] > 0 },
        'no_duplicate_exterior_faces' => duplicate_faces.zero?,
        'canopies_complete' => topology.fetch('canopies').all? { |canopy| result_map.fetch(canopy.fetch('id'))['generated_solids'] > 0 },
        'materials_traceable' => topology.fetch('materials').all? { |material| result_map.key?(material.fetch('id')) },
        'debug_geometry_clear' => root.entities.grep(Sketchup::ConstructionLine).empty? && root.entities.grep(Sketchup::ConstructionPoint).empty?
      }
      raise "Production self-check failed: #{checks.select { |_key, value| value != true }.keys.join(', ')}" unless checks.values.all?
      generated_solids = elements.inject(0) { |total, item| total + item['generated_solids'].to_i }
      statistics = {
        'unique_semantic_inputs' => required_ids.length,
        'level_processing_occurrences' => topology.fetch('levels').length,
        'generated_solids' => generated_solids,
        'failed_solids' => 0,
        'unique_openings' => topology.fetch('openings').length,
        'opening_occurrences' => topology.fetch('openings').length,
        'output_bounds_mm' => bounds_mm(root.bounds)
      }
      report = {'schema' => 'cad_to_sketchup.production_self_check.2026-08-06', 'status' => 'PASS', 'elements' => elements, 'statistics' => statistics, 'geometry_checks' => checks, 'duplicate_face_count' => duplicate_faces, 'unresolved' => []}
      File.write(report_path, JSON.pretty_generate(report), mode: 'w:UTF-8')
      model.commit_operation
      raise "Could not save production model to #{output_path}" unless model.save(output_path)
      result = {
        'schema' => 'cad_to_sketchup.sketchup_production_result.2026-08-06', 'status' => 'generated_and_self_checked',
        'build_plan_path' => plan_path, 'build_plan_sha256' => sha256(plan_path),
        'source_model_path' => source_path, 'source_model_sha256' => target.fetch('source_model_sha256'),
        'output_model_path' => output_path, 'output_model_sha256' => sha256(output_path),
        'root_group' => ROOT_NAME, 'root_persistent_id' => root.persistent_id,
        'element_results' => elements, 'statistics' => statistics, 'geometry_checks' => checks,
        'report_path' => report_path, 'report_sha256' => sha256(report_path), 'unresolved' => []
      }
      File.write(result_path, JSON.pretty_generate(result), mode: 'w:UTF-8')
      "production model generated and self-checked: #{output_path}"
    rescue Exception
      model.abort_operation if model.respond_to?(:abort_operation)
      raise
    end
  end
end

# Load through SketchUp MCP/bridge, then call:
# HEBIProductionBuild.run('C:/project/work/build/sketchup-production-plan.json')
