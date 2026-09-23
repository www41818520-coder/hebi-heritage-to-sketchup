# frozen_string_literal: true

module HEBIRoofShellGeometry
  # CAD section thickness is vertical. Independent normal extrusions leave a
  # wedge at a shared ridge; build one shell from shared boundary ownership.
  def self.build(group, patches, thickness_mm)
    raise 'Roof thickness must be positive' unless thickness_mm.to_f > 0
    edges={}
    patches.each do |raw|
      points=raw.map { |p| p.map(&:to_f) }
      area=points.each_with_index.sum { |p,i| q=points[(i+1)%points.length]; p[0]*q[1]-q[0]*p[1] }
      points.reverse! if area<0
      bottom=points.map { |p| Geom::Point3d.new(*p.map(&:mm)) }
      top=points.map { |p| Geom::Point3d.new(p[0].mm,p[1].mm,(p[2]+thickness_mm).mm) }
      lower=group.entities.add_face(bottom.reverse)
      upper=group.entities.add_face(top)
      raise 'Invalid roof patch' unless lower && upper
      lower.reverse! if lower.normal.z>0
      upper.reverse! if upper.normal.z<0
      points.each_with_index do |p,i|
        q=points[(i+1)%points.length]
        key=[p.map { |v| v.round(4) },q.map { |v| v.round(4) }].sort
        (edges[key] ||= []) << [p,q]
      end
    end
    edges.each_value do |owners|
      raise 'More than two roof patches share an edge' if owners.length>2
      next if owners.length==2
      p,q=owners.first
      polygon=[p,q,[q[0],q[1],q[2]+thickness_mm],[p[0],p[1],p[2]+thickness_mm]]
      raise 'Invalid roof perimeter side' unless group.entities.add_face(polygon.map { |v| Geom::Point3d.new(*v.map(&:mm)) })
    end
    raise 'Roof shell is not a closed solid' unless group.manifold? && group.volume>0
    group
  end
end
