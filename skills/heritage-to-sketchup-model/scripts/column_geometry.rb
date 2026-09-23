# frozen_string_literal: true
require 'digest'
require 'json'

module HEBIColumnGeometry
  def self.build(root,records,dictionary='HEBI_TOPOLOGY')
    records.map do |record|
      points=record.fetch('footprint')
      ox=points.map(&:first).min; oy=points.map(&:last).min
      profile=points.map { |x,y| [(x-ox).round(1),(y-oy).round(1)] }
      profile=profile.rotate(profile.each_index.min_by { |i| profile[i] })
      voids=record.fetch('voids',[]).map { |loop| loop.map { |x,y| [(x-ox).round(1),(y-oy).round(1)] } }
      height=record.fetch('top_elevation_mm')-record.fetch('base_elevation_mm')
      signature=Digest::SHA256.hexdigest(JSON.generate([profile,voids,height.round(1)]))[0,16]
      name="CAD_STRUCTURAL_COLUMN_#{signature}"
      definition=root.model.definitions[name]
      unless definition
        definition=root.model.definitions.add(name)
        face=definition.entities.add_face(profile.map { |x,y| Geom::Point3d.new(x.mm,y.mm,0) })
        raise 'Invalid measured column profile' unless face
        face.reverse! if face.normal.z<0
        voids.each do |loop|
          hole=definition.entities.add_face(loop.map { |x,y| Geom::Point3d.new(x.mm,y.mm,0) })
          raise 'Invalid column hollow section' unless hole
          hole.erase!
        end
        face=definition.entities.grep(Sketchup::Face).max_by(&:area)
        raise 'Column section has no face' unless face
        face.reverse! if face.normal.z<0
        face.pushpull(height.mm)
      end
      instance=root.entities.add_instance(definition,Geom::Transformation.translation([ox.mm,oy.mm,record.fetch('base_elevation_mm').mm]))
      instance.name="COLUMN_#{record.fetch('id')}"
      instance.set_attribute(dictionary,'topology_id',record.fetch('id'))
      instance.set_attribute(dictionary,'kind','column')
      raise 'Column is not a solid component' unless instance.manifold?
      instance
    end
  end
end
