# frozen_string_literal: true

require 'base64'
require 'fileutils'
require 'json'
require 'socket'
require 'thread'
require 'sketchup.rb'

module HeritageSketchUpBridge
  extend self

  HOST = '127.0.0.1'
  BASE_PORT = 17_860
  CONFIG_PATH = File.join(__dir__, 'heritage_su_bridge_config.json')

  def config
    @config ||= begin
      File.exist?(CONFIG_PATH) ? JSON.parse(File.read(CONFIG_PATH, mode: 'r:BOM|UTF-8')) : {}
    rescue
      {}
    end
  end

  def registry_dir
    configured = config['registry_dir'].to_s
    configured.empty? ? File.join(Dir.home, 'heritage_su_bridge_registry') : configured
  end

  def log_path
    configured = config['log_path'].to_s
    configured.empty? ? File.join(registry_dir, 'bridge.log') : configured
  end

  def log(message)
    FileUtils.mkdir_p(registry_dir)
    File.open(log_path, 'a:BOM|UTF-8') { |file| file.puts("#{Time.now} #{message}") }
  rescue
    nil
  end

  def registry_path
    File.join(registry_dir, "sketchup_#{Process.pid}.json")
  end

  def start
    return true if running?

    require 'fileutils'
    FileUtils.mkdir_p(registry_dir)
    @requests = Queue.new
    @port = find_port
    @server = TCPServer.new(HOST, @port)
    @server_thread = Thread.new do
      loop do
        client = @server.accept
        Thread.new(client) { |socket| handle_client(socket) }
      rescue IOError
        break
      rescue StandardError => e
        log("accept error #{e.class}: #{e.message}")
      end
    end
    @request_timer = UI.start_timer(0.05, true) { process_pending_requests }
    @registry_timer = UI.start_timer(2.0, true) { write_registry }
    write_registry
    log("listening #{HOST}:#{@port} pid=#{Process.pid}")
    true
  rescue Exception => e
    log("start error #{e.class}: #{e.message}")
    false
  end

  def stop
    UI.stop_timer(@request_timer) if @request_timer
    UI.stop_timer(@registry_timer) if @registry_timer
    @request_timer = nil
    @registry_timer = nil
    @server&.close
    @server = nil
    @server_thread&.kill
    @server_thread = nil
    File.delete(registry_path) if File.exist?(registry_path)
    true
  rescue
    false
  end

  def running?
    @server && !@server.closed?
  end

  def find_port
    start_port = BASE_PORT + (Process.pid % 1000)
    1000.times do |index|
      port = BASE_PORT + ((start_port - BASE_PORT + index) % 1000)
      begin
        server = TCPServer.new(HOST, port)
        server.close
        return port
      rescue
        next
      end
    end
    raise 'No free Codex SketchUp bridge port'
  end

  def write_registry
    model = Sketchup.active_model
    payload = {
      pid: Process.pid,
      port: @port,
      model_path: model&.path.to_s,
      model_title: model&.title.to_s,
      updated_at: Time.now.to_s
    }
    File.write(registry_path, JSON.pretty_generate(payload), mode: 'w:BOM|UTF-8')
  rescue Exception => e
    log("registry error #{e.class}: #{e.message}")
  end

  def process_pending_requests
    until @requests.empty?
      request = @requests.pop(true)
      begin
        result = TOPLEVEL_BINDING.eval(request[:code], '(codex-su-bridge)')
        write_registry
        request[:queue] << [:ok, result.inspect]
      rescue Exception => e
        backtrace = Array(e.backtrace).first(16).join("\n")
        request[:queue] << [:err, "#{e.class}: #{e.message}\n#{backtrace}"]
      end
    end
  rescue ThreadError
    nil
  end

  def handle_client(socket)
    socket.each_line do |line|
      encoded = line.strip
      next if encoded.empty?

      queue = Queue.new
      @requests << { code: Base64.strict_decode64(encoded), queue: queue }
      status, message = queue.pop
      socket.write("#{status}\t#{Base64.strict_encode64(message.to_s)}\n")
      socket.flush
    end
  rescue Exception => e
    log("client error #{e.class}: #{e.message}")
  ensure
    socket.close rescue nil
  end
end

unless file_loaded?(__FILE__)
  UI.menu('Plugins').add_submenu('HEBI Heritage Bridge').add_item('Restart Bridge') do
    HeritageSketchUpBridge.stop
    HeritageSketchUpBridge.start
  end
  HeritageSketchUpBridge.start
  file_loaded(__FILE__)
end
