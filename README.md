# Zelos extension for OPAL-RT

## Features

- 📡 **Real-time signal streaming** - Stream all model signals to Zelos with hierarchical event structure matching RT-LAB
- 🎛️ **Read & write signals** - Read and set signal values, parameters, and variables from the Zelos App
- 📊 **Model introspection** - Browse signals, parameters, control signals, and workspace variables
- 🔌 **Auto-discovery** - Automatically detects RT-LAB installation and discovers model contents on connect

## Quick Start

1. **Install** the extension from the Zelos App
2. **Configure** the path to your RT-LAB project file (`.llp`)
3. **Start** the extension to begin streaming data
4. **View** real-time signals in your Zelos App

## Configuration

All configuration is managed through the Zelos App settings interface.

### Required Settings

- **Project File**: Path to your RT-LAB project (`.llp`) file

### Optional Settings

- **Acquisition Time Step (ms)**: Sampling time step for data acquisition (default: 1)
- **Poll Interval (seconds)**: Delay between acquisition frames (default: 1.0)
- **RT-LAB Install Path**: Path to your RT-LAB version directory — leave blank for auto-discovery
- **Log Level**: Logging verbosity (default: INFO)

## Actions

The extension provides several actions accessible from the Zelos App:

- **Get Status** - View model state, signal counts, and connection info
- **List Signals / Parameters / Variables / Control Signals** - Browse all model contents
- **Read Signal / Parameter / Variable** - Read current values
- **Set Signal / Parameter / Variable** - Write new values
- **Read / Set Control Signals** - Interact with control signals by subsystem
- **Set Poll Interval** - Adjust acquisition speed at runtime

## Support

For help and support:

- 📖 [Zelos Documentation](https://docs.zeloscloud.io)
- 📧 help@zeloscloud.io

## License

MIT License - see [LICENSE](LICENSE) for details.
