# frozen_string_literal: true

require 'digest'
require 'fileutils'
require 'json'
require 'sketchup.rb'

module HEBITopologyWhiteModel
  ROOT_NAME = 'HEBI_TOPOLOGY_WHITE_MODEL'

  module_function

  def mm(value)
    value.to_f.mm
  end

  def point2(value, z)
    Geom::Point3d.new(mm(value[0]), mm(value[1]), mm(z))
  end

  def point3(value)
    Geom::Point3d.new(mm(value[0]), mm(value[1]), mm(value[2]))
  end

  def vector3(value)
    Geom::Vector3d.new(value[0].to_f, value[1].to_f, value[2].to_f)
  end

  def resolve(project_root, value)
    path = value.to_s
    File.expand_path(path, project_root)
  end

  def polygon_area(points)
    points.each_with_index.sum do |point, index|
      following = points[(index + 1) % points.length]
      point[0].to_f * following[1].to_f - following[0].to_f * point[1].to_f
    end / 2.0
  end

  def add_prism(entities, polygon, bottom_mm, top_mm)
    raise 'Prism top must be above bottom' unless top_mm.to_f > bottom_mm.to_f

    face = entities.add_face(polygon.map { |point| point2(point, bottom_mm) })
    raise 'Cannot create prism base face' unless face&.valid?

    face.reverse! if face.normal.z < 0
    face.pushpull(mm(top_mm.to_f - bottom_mm.to_f))
  end

  def inward_normal(start_point, end_point, orientation)
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    raise 'Zero-length wall segment' if length <= 1e-9

    orientation >= 0 ? [-dy / length, dx / length] : [dy / length, -dx / length]
  end

  def wall_distance(wall, position)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    return 0.0 if length <= 1e-9

    ((position[0].to_f - start_point[0].to_f) * dx + (position[1].to_f - start_point[1].to_f) * dy) / length
  end

  def add_wall_block(entities, wall, orientation, from_distance, to_distance, bottom, top)
    return if to_distance.to_f - from_distance.to_f <= 0.1 || top.to_f - bottom.to_f <= 0.1

    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    ux = dx / length
    uy = dy / length
    inward = inward_normal(start_point, end_point, orientation)
    thickness = wall.fetch('thickness_mm').to_f
    outer_start = [start_point[0].to_f + ux * from_distance, start_point[1].to_f + uy * from_distance]
    outer_end = [start_point[0].to_f + ux * to_distance, start_point[1].to_f + uy * to_distance]
    inner_end = [outer_end[0] + inward[0] * thickness, outer_end[1] + inward[1] * thickness]
    inner_start = [outer_start[0] + inward[0] * thickness, outer_start[1] + inward[1] * thickness]
    add_prism(entities, [outer_start, outer_end, inner_end, inner_start], bottom, top)
  end

  def add_wall_panel_cell(entities, wall, from_distance, to_distance, bottom, top)
    return if to_distance.to_f - from_distance.to_f <= 0.1 || top.to_f - bottom.to_f <= 0.1

    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    ux = dx / length
    uy = dy / length
    first = [start_point[0].to_f + ux * from_distance, start_point[1].to_f + uy * from_distance]
    second = [start_point[0].to_f + ux * to_distance, start_point[1].to_f + uy * to_distance]
    face = entities.add_face(point2(first, bottom), point2(second, bottom), point2(second, top), point2(first, top))
    raise 'Cannot create wall facade cell' unless face&.valid?
  end

  def add_wall_profile_face(entities, wall, from_distance, to_distance, bottom, from_top, to_top)
    return if to_distance.to_f - from_distance.to_f <= 0.1
    return if [from_top.to_f, to_top.to_f].max - bottom.to_f <= 0.1

    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    ux = dx / length
    uy = dy / length
    profile = [[from_distance.to_f, bottom.to_f], [to_distance.to_f, bottom.to_f]]
    profile << [to_distance.to_f, to_top.to_f] if to_top.to_f - bottom.to_f > 0.1
    profile << [from_distance.to_f, from_top.to_f] if from_top.to_f - bottom.to_f > 0.1
    face = entities.add_face(profile.map { |station, z| point2([start_point[0].to_f + ux * station, start_point[1].to_f + uy * station], z) })
    raise 'Cannot create wall top-profile facade cell' unless face&.valid?
  end

  def solidify_wall_panel(entities, wall, orientation)
    entities.grep(Sketchup::Edge).each { |edge| edge.erase! if edge.valid? && edge.faces.empty? }
    loop do
      seam = entities.grep(Sketchup::Edge).find do |edge|
        faces = edge.faces
        faces.length == 2 && faces[0].normal.parallel?(faces[1].normal)
      end
      break unless seam

      seam.erase!
    end
    inward = inward_normal(wall.fetch('start'), wall.fetch('end'), orientation)
    direction = Geom::Vector3d.new(inward[0], inward[1], 0)
    thickness = wall.fetch('thickness_mm').to_f
    facade_faces = entities.grep(Sketchup::Face)
    raise 'Wall facade cells did not form a face' if facade_faces.empty?
    facade_faces.each do |face|
      next unless face.valid?

      face.reverse! if face.normal.dot(direction) < 0
      face.pushpull(mm(thickness))
    end
  end

  def add_wall_profile_block(entities, wall, orientation, from_distance, to_distance, bottom, from_top, to_top)
    return if to_distance.to_f - from_distance.to_f <= 0.1
    return if [from_top.to_f, to_top.to_f].max - bottom.to_f <= 0.1

    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    length = Math.sqrt(dx * dx + dy * dy)
    ux = dx / length
    uy = dy / length
    inward = inward_normal(start_point, end_point, orientation)
    thickness = wall.fetch('thickness_mm').to_f
    profile = [[from_distance.to_f, bottom.to_f], [to_distance.to_f, bottom.to_f]]
    profile << [to_distance.to_f, to_top.to_f] if to_top.to_f - bottom.to_f > 0.1
    profile << [from_distance.to_f, from_top.to_f] if from_top.to_f - bottom.to_f > 0.1
    outer = profile.map { |station, z| Geom::Point3d.new(mm(start_point[0].to_f + ux * station), mm(start_point[1].to_f + uy * station), mm(z)) }
    inner = outer.map { |point| Geom::Point3d.new(point.x + mm(inward[0] * thickness), point.y + mm(inward[1] * thickness), point.z) }
    outer_face = entities.add_face(outer)
    inner_face = entities.add_face(inner.reverse)
    raise 'Cannot create wall top-profile faces' unless outer_face&.valid? && inner_face&.valid?
    outer.each_index do |index|
      next_index = (index + 1) % outer.length
      entities.add_face(outer[index], outer[next_index], inner[next_index], inner[index])
    end
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

  def build_wall(entities, wall, level, openings, orientation)
    start_point = wall.fetch('start')
    end_point = wall.fetch('end')
    length = Math.sqrt((end_point[0].to_f - start_point[0].to_f)**2 + (end_point[1].to_f - start_point[1].to_f)**2)
    base = level.fetch('elevation_mm').to_f
    top = level.fetch('top_elevation_mm').to_f
    profile = wall['top_profile']
    top_points = profile || [[0.0, top], [length, top]]
    facade_profile = [[0.0, base], [length, base]] + top_points.reverse.map { |station, elevation| [station.to_f, elevation.to_f] }
    dx = end_point[0].to_f - start_point[0].to_f
    dy = end_point[1].to_f - start_point[1].to_f
    ux = dx / length
    uy = dy / length
    facade = entities.add_face(facade_profile.map { |station, z| point2([start_point[0].to_f + ux * station, start_point[1].to_f + uy * station], z) })
    raise 'Cannot create complete wall facade' unless facade&.valid?

    openings.each do |opening|
      center = wall_distance(wall, opening.fetch('position'))
      half = opening.fetch('width_mm').to_f / 2.0
      left = [[center - half, 0.0].max, length].min
      right = [[center + half, 0.0].max, length].min
      sill = [[opening.fetch('sill_elevation_mm').to_f, base].max, top].min
      head = [[sill + opening.fetch('height_mm').to_f, base].max, top].min
      next if right - left <= 0.1 || head - sill <= 0.1

      rectangle = [[left, sill], [right, sill], [right, head], [left, head]].map do |station, z|
        point2([start_point[0].to_f + ux * station, start_point[1].to_f + uy * station], z)
      end
      opening_face = entities.add_face(rectangle)
      raise "Cannot create true opening #{opening.fetch('id')}" unless opening_face&.valid?

      opening_face.erase!
    end
    solidify_wall_panel(entities, wall, orientation)
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

  def build_levels(root, plan)
    walls = plan.fetch('walls').to_h { |wall| [wall.fetch('id'), wall] }
    wall_openings = plan.fetch('openings') + plan.fetch('curtain_walls').map { |record| curtain_opening(record) }
    openings = wall_openings.group_by { |opening| opening.fetch('host_wall_id') }
    plan.fetch('levels').each do |level|
      group = root.entities.add_group
      group.name = "TOPO_WALL_RING_#{level.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', level.fetch('id'))
      orientation = polygon_area(level.fetch('footprint'))
      merged = nil
      level.fetch('exterior_wall_ring').fetch('wall_ids').each do |wall_id|
        temporary = group.entities.add_group
        build_wall(temporary.entities, walls.fetch(wall_id), level, openings.fetch(wall_id, []), orientation)
        raise "Wall #{wall_id} is not solid" unless temporary.manifold?
        merged = merged ? merged.union(temporary) : temporary
        raise "Cannot merge wall #{wall_id}" unless merged && merged.manifold?
      end
      merged.explode
      raise 'Exterior wall ring is not a closed solid' unless group.manifold?
      group.set_attribute('HEBI_TOPOLOGY', 'wall_generation', 'merged_facade_panels_with_true_openings')
    end
  end

  def build_internal_walls(root, records)
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
      group = root.entities.add_group
      group.name = "TOPO_INTERNAL_WALL_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      add_prism(group.entities, boundary, record.fetch('base_elevation_mm').to_f, record.fetch('top_elevation_mm').to_f)
    end
  end

  def build_prism_collection(root, records, prefix, bottom_key, thickness_key)
    records.each do |record|
      group = root.entities.add_group
      group.name = "#{prefix}_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      top = record.fetch(bottom_key).to_f
      thickness = record.fetch(thickness_key).to_f
      add_prism(group.entities, record.fetch('boundary', record.fetch('footprint', nil)), top - thickness, top)
    end
  end

  def build_roofs(root, roofs)
    roofs.each do |roof|
      group = root.entities.add_group
      group.name = "TOPO_ROOF_#{roof.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', roof.fetch('id'))
      if roof.fetch('topology_type') == 'flat'
        bottom = roof.fetch('base_elevation_mm').to_f
        add_prism(group.entities, roof.fetch('boundary'), bottom, bottom + roof.fetch('thickness_mm').to_f)
      else
        require_relative 'roof_shell_geometry'
        HEBIRoofShellGeometry.build(group, roof.fetch('shell_faces'), roof.fetch('thickness_mm').to_f)
      end
    end
  end

  def build_roof_lights(root, records, roofs)
    roof_by_id = roofs.to_h { |roof| [roof.fetch('id'), roof] }
    records.each do |record|
      group = root.entities.add_group
      group.name = "TOPO_ROOF_LIGHT_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      face = group.entities.add_face(record.fetch('boundary').map { |point| point3(point) })
      raise "Cannot create roof light #{record.fetch('id')}" unless face&.valid?
      face.reverse! if face.normal.z < 0
      unless record.fetch('true_opening')
        host = roof_by_id.fetch(record.fetch('host_roof_id'))
        offset = Geom::Vector3d.new(0, 0, mm(host.fetch('thickness_mm').to_f + 1.0))
        group.transform!(Geom::Transformation.translation(offset))
      end
      material = group.model.materials['HEBI_TOPO_ROOF_LIGHT'] || group.model.materials.add('HEBI_TOPO_ROOF_LIGHT')
      material.color = Sketchup::Color.new(90, 175, 220)
      material.alpha = 0.55
      face.material = material
      face.back_material = material
    end
  end

  def build_canopies(root, records)
    records.each do |record|
      group = root.entities.add_group
      group.name = "TOPO_CANOPY_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      bottom = record.fetch('base_elevation_mm').to_f
      add_prism(group.entities, record.fetch('footprint'), bottom, bottom + record.fetch('thickness_mm').to_f)
    end
  end

  def build_curtain_walls(root, records)
    records.each do |record|
      group = root.entities.add_group
      group.name = "TOPO_CURTAIN_WALL_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      group.entities.add_face(record.fetch('boundary').map { |point| point3(point) })
    end
  end

  def build_sweeps(root, records)
    records.each do |record|
      group = root.entities.add_group
      group.name = "TOPO_SWEEP_PATH_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      path = record.fetch('path').map { |point| point3(point) }
      path.each_cons(2) { |first, second| group.entities.add_line(first, second) }
      profile_origin = path.first
      basis = record.fetch('profile_basis')
      u_axis = vector3(basis.fetch('u_axis'))
      v_axis = vector3(basis.fetch('v_axis'))
      profile = record.fetch('profile').map do |point|
        profile_origin.offset(u_axis, mm(point[0])).offset(v_axis, mm(point[1]))
      end
      group.entities.add_face(profile)
    end
  end

  def export_review_views(model, root, plan, review_dir)
    FileUtils.mkdir_p(review_dir)
    view = model.active_view
    owned_groups = root.entities.select { |entity| entity.is_a?(Sketchup::Group) || entity.is_a?(Sketchup::ComponentInstance) }
    plan.fetch('registrations').map do |registration|
      visible_ids = registration.fetch('visible_topology_ids').map(&:to_s)
      original_hidden = owned_groups.to_h { |group| [group, group.hidden?] }
      section_plane = nil
      begin
      owned_groups.each do |group|
        topology_id = group.get_attribute('HEBI_TOPOLOGY', 'topology_id').to_s
        group.hidden = !visible_ids.include?(topology_id)
      end
      visible_groups = owned_groups.reject(&:hidden?)
      raise "No visible topology for #{registration.fetch('source_view_id')}" if visible_groups.empty?

      bounds = Geom::BoundingBox.new
      visible_groups.each { |group| bounds.add(group.bounds) }
      x_axis = vector3(registration.fetch('transform').fetch('x_axis'))
      y_axis = vector3(registration.fetch('transform').fetch('y_axis'))
      normal = x_axis.cross(y_axis)
      raise "Invalid registration normal for #{registration.fetch('source_view_id')}" if normal.length <= 1e-9

      normal.normalize!
      source_bounds = registration.fetch('source_bounds')
      source_width = source_bounds.fetch('xmax').to_f - source_bounds.fetch('xmin').to_f
      source_height = source_bounds.fetch('ymax').to_f - source_bounds.fetch('ymin').to_f
      raise "Invalid source bounds for #{registration.fetch('source_view_id')}" if source_width <= 0 || source_height <= 0
      transform = registration.fetch('transform')
      source_origin = transform.fetch('source_origin').map(&:to_f)
      source_center = [(source_bounds.fetch('xmin').to_f + source_bounds.fetch('xmax').to_f) / 2.0,
                       (source_bounds.fetch('ymin').to_f + source_bounds.fetch('ymax').to_f) / 2.0]
      target = point3(transform.fetch('origin'))
      target = target.offset(x_axis, mm((source_center[0] - source_origin[0]) * transform.fetch('scale').to_f))
      target = target.offset(y_axis, mm((source_center[1] - source_origin[1]) * transform.fetch('scale').to_f))
      distance = [bounds.diagonal.to_f * 2.5, 10_000.mm].max
      eye = target.offset(normal, distance)
      camera = Sketchup::Camera.new(eye, target, y_axis)
      camera.perspective = false
      view.camera = camera
      section_plane = nil
      if registration.fetch('role') == 'section' || registration.fetch('role') == 'plan'
        section_origin = point3(registration.fetch('transform').fetch('origin'))
        if registration.fetch('role') == 'plan'
          cut_elevation = registration.fetch('section_cut_elevation_mm')
          section_origin = Geom::Point3d.new(section_origin.x, section_origin.y, mm(cut_elevation))
        end
        section_normal = registration.fetch('role') == 'plan' ? normal.reverse : normal
        section_plane = model.entities.add_section_plane(section_origin, section_normal)
        section_plane.activate if section_plane.respond_to?(:activate)
      end
      # zoom_extents() considers unrelated top-level entities, guides and
      # section planes in the active model. Frame the owned topology root
      # explicitly so each evidence image is readable and repeatable.
      if source_width >= source_height
        image_width = 2400
        image_height = [(2400.0 * source_height / source_width).round, 1].max
      else
        image_height = 2400
        image_width = [(2400.0 * source_width / source_height).round, 1].max
      end
      # In SketchUp 2024 Camera#height is the full orthographic view height.
      # write_image applies the requested bitmap aspect ratio, so matching the
      # CAD frame requires the full source height (not half-height and not a
      # correction based on the live application viewport).
      camera.height = mm(source_height * transform.fetch('scale').to_f) if camera.respond_to?(:height=)
      # Re-assign after changing height: SketchUp copies the Camera object when
      # it is assigned to the view, so mutating the earlier Ruby object alone
      # does not update the active view.
      view.camera = camera
      output = File.join(review_dir, "#{registration.fetch('source_view_id')}.jpg")
      view.write_image(output, image_width, image_height, true, 0.95)
      {
        'source_view_id' => registration.fetch('source_view_id'),
        'role' => registration.fetch('role'),
        'path' => output,
        'sha256' => Digest::SHA256.file(output).hexdigest
      }
      ensure
        section_plane.erase! if section_plane&.valid?
        original_hidden.each { |group, hidden| group.hidden = hidden if group.valid? }
      end
    end
  end

  def run(plan_path)
    plan_path = File.expand_path(plan_path)
    plan = JSON.parse(File.read(plan_path, mode: 'r:BOM|UTF-8'))
    raise 'Topology plan is not execution-authorized' unless plan['execution_allowed'] == true && plan['status'] == 'ready_for_white_model'

    project_root = plan.fetch('project_root')
    output = plan.fetch('white_model_output')
    model_path = resolve(project_root, output.fetch('model_path'))
    result_path = resolve(project_root, output.fetch('result_path'))
    review_dir = resolve(project_root, output.fetch('review_dir'))
    FileUtils.mkdir_p(File.dirname(model_path))
    FileUtils.mkdir_p(File.dirname(result_path))
    model = Sketchup.active_model
    model.close_active while model.active_path
    model.start_operation('HEBI topology white model', true)
    begin
      model.entities.grep(Sketchup::Group).select { |group| group.name == ROOT_NAME }.each(&:erase!)
      root = model.entities.add_group
      root.name = ROOT_NAME
      root.set_attribute('HEBI_TOPOLOGY', 'plan_sha256', Digest::SHA256.file(plan_path).hexdigest)
      build_levels(root, plan)
      build_internal_walls(root, plan.fetch('internal_walls'))
      require_relative 'column_geometry'
      HEBIColumnGeometry.build(root, plan.fetch('columns', []))
      build_prism_collection(root, plan.fetch('slabs'), 'TOPO_SLAB', 'top_elevation_mm', 'thickness_mm')
      build_roofs(root, plan.fetch('roofs'))
      build_roof_lights(root, plan.fetch('roof_lights'), plan.fetch('roofs'))
      build_curtain_walls(root, plan.fetch('curtain_walls'))
      build_sweeps(root, plan.fetch('sweeps'))
      build_canopies(root, plan.fetch('canopies'))
      require_relative 'access_geometry'
      HEBIAccessGeometry.build(root, plan.fetch('access_elements', []), 'TOPO_ACCESS')
      model.commit_operation
      raise "Could not save topology white model to #{model_path}" unless model.save(model_path)
      review_views = export_review_views(model, root, plan, review_dir)
      model.save(model_path)
      result = {
        'schema' => 'cad_to_sketchup.topology_white_model_result.2026-08-05',
        'status' => 'generated',
        'topology_plan_path' => plan_path,
        'topology_plan_sha256' => Digest::SHA256.file(plan_path).hexdigest,
        'model_path' => model_path,
        'model_sha256' => Digest::SHA256.file(model_path).hexdigest,
        'root_group' => ROOT_NAME,
        'root_persistent_id' => root.persistent_id,
        'review_views' => review_views,
        'unresolved' => []
      }
      File.write(result_path, JSON.pretty_generate(result), mode: 'w:UTF-8')
      "topology white model generated: #{result_path}"
    rescue Exception
      model.abort_operation if model.respond_to?(:abort_operation)
      raise
    end
  end
end

# Call through the bridge after loading:
# HEBITopologyWhiteModel.run('C:/project/work/topology/topology-plan.json')
