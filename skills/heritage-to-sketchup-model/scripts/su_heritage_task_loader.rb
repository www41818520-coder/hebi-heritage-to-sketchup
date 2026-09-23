require 'json'
require 'sketchup.rb'

module HeritageSuTaskLoader
  extend self

  CONFIG_PATH = File.join(__dir__, 'heritage_su_task_config.json')
  FALLBACK_LOG_PATH = File.join(__dir__, 'heritage_su_task_loader_fallback.log')

  def config
    @config ||= JSON.parse(File.read(CONFIG_PATH, mode: 'r:BOM|UTF-8'))
  end

  def log_path
    config['log_path'].to_s.empty? ? FALLBACK_LOG_PATH : config['log_path']
  rescue
    FALLBACK_LOG_PATH
  end

  def status_path
    config['status_path']
  rescue
    nil
  end

  def log(message)
    File.open(log_path, 'a:BOM|UTF-8') { |file| file.puts("#{Time.now} #{message}") }
  rescue
    nil
  end

  def write_status(text)
    path = status_path
    return if path.to_s.empty?

    File.write(path, text, mode: 'w:BOM|UTF-8')
  rescue
    nil
  end

  def disable_loader
    disabled = __FILE__ + '.disabled'
    File.rename(__FILE__, disabled) if File.exist?(__FILE__) && !File.exist?(disabled)
  rescue
    nil
  end

  def normalized_path(path)
    File.expand_path(path.to_s).tr('\\', '/')
  end

  def open_requested_model
    model_path = config['model_path'].to_s
    return if model_path.empty?

    current = Sketchup.active_model.path.to_s
    return if !current.empty? && normalized_path(current) == normalized_path(model_path)

    log("opening model #{model_path}")
    Sketchup.open_file(model_path)
  end

  def run_task
    task_script = config['task_script'].to_s
    raise 'Missing task_script in heritage_su_task_config.json' if task_script.empty?
    raise "Task script not found: #{task_script}" unless File.exist?(task_script)

    open_requested_model
    log("loading task #{task_script}")
    load task_script
    log('task returned')
    write_status("OK\n#{Time.now}\ntask_script=#{task_script}\nmodel_path=#{Sketchup.active_model.path}\n")
  end

  def run
    log('loader loaded')
    UI.start_timer(3.0, false) do
      begin
        run_task
      rescue Exception => e
        log("ERROR #{e.class}: #{e.message}")
        write_status("ERROR\n#{e.class}\n#{e.message}\n#{e.backtrace&.join("\n")}\n")
      ensure
        disable_loader
      end
    end
  end
end

HeritageSuTaskLoader.run
