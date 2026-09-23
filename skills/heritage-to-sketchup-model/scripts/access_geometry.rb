# frozen_string_literal: true

module HEBIAccessGeometry
  def self.build(root, records, prefix)
    records.map do |record|
      group = block_given? ? yield(record) : root.entities.add_group
      group.name = "#{prefix}_#{record.fetch('id')}"
      group.set_attribute('HEBI_TOPOLOGY', 'topology_id', record.fetch('id'))
      record.fetch('shell_faces').each do |points|
        face = group.entities.add_face(points.map { |p| Geom::Point3d.new(*p.map { |v| v.to_f.mm }) })
        raise "Invalid entrance face: #{record.fetch('id')}" unless face
      end
      raise "Entrance is not a closed solid: #{record.fetch('id')}" unless group.manifold? && group.volume > 0
      group
    end
  end
end
