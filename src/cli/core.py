"""
src/cli/core.py
核心模块，定义了 CLI 的基础组件：抽象命令类和应用主类。
"""
import json
import argparse
import abc
from typing import Dict, Type, List, Optional
import sys
import shlex
from toolboxs import translate_error

class ArgumentParserError(Exception):
    """
    自定义异常类，用于捕获 ArgumentParser 的错误。
    默认的 argparse 会在出错时调用 sys.exit()，
    在交互模式下，我们希望捕获错误并继续运行，而不是直接退出程序。
    """
    pass

class NoExitArgumentParser(argparse.ArgumentParser):
    """
    自定义的参数解析器。
    重写了 error 和 exit 方法，使其抛出 ArgumentParserError 异常，
    从而阻止程序直接退出。
    """
    def error(self, message):
        """当参数解析出错时调用。"""
        raise ArgumentParserError(message)

    def exit(self, status=0, message=None):
        """当调用 --help 或解析错误需要退出时调用。"""
        if message:
            print(message, file=sys.stderr)
        raise ArgumentParserError(f"Exited with status {status}")

class BaseCommand(abc.ABC):
    """
    所有 CLI 命令的抽象基类。
    每个具体的命令都需要继承此类并实现其抽象方法。
    """
    
    @property
    @abc.abstractmethod
    def name(self) -> str:
        """
        命令的名称，用于在命令行中调用该命令。
        例如，如果 name 返回 "greet"，则可通过 `mycli greet` 调用。
        """
        pass

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """
        命令的描述信息，显示在帮助文本中。
        """
        pass

    @abc.abstractmethod
    def configure_parser(self, parser: argparse.ArgumentParser) -> None:
        """
        为当前命令配置参数解析器。
        在这里添加该命令所需的特定参数（如 --name, -v 等）。
        
        Args:
            parser: 为该命令创建的子解析器实例。
        """
        pass

    @abc.abstractmethod
    def execute(self, args: argparse.Namespace) -> int:
        """
        执行命令的核心逻辑。
        
        Args:
            args: 解析后的命令行参数命名空间。
            
        Returns:
            整数退出码，0 表示成功，非 0 表示失败。
        """
        pass

class CLIApp:
    """
    CLI 应用的主类，负责管理所有已注册的命令。
    支持传统的单次脚本调用模式和交互式 REPL 模式。
    """

    def __init__(self, prog_name: str = "app", description: str = "可扩展的命令行工具"):
        """
        初始化 CLI 应用。
        
        Args:
            prog_name: 程序名称，显示在提示符和帮助信息中。
            description: 程序的整体描述。
        """
        self.prog_name = prog_name
        # 使用自定义的 NoExitArgumentParser，确保在交互模式下解析错误不会导致程序退出
        self.parser = NoExitArgumentParser(prog=prog_name, description=description)
        
        # 添加子命令解析器容器
        self.subparsers = self.parser.add_subparsers(title="所有命令", dest="command", required=True)
        # 存储已注册的命令实例
        self._commands: Dict[str, BaseCommand] = {}

    def register_command(self, command_cls: Type[BaseCommand]) -> None:
        """
        注册一个新的命令类。
        
        Args:
            command_cls: 继承自 BaseCommand 的命令类。
        """
        command = command_cls()
        if command.name in self._commands:
            raise ValueError(f"Command '{command.name}' is already registered.")
        
        self._commands[command.name] = command
        
        # 为新命令添加子解析器
        cmd_parser = self.subparsers.add_parser(
            command.name, 
            help=command.description,
            description=command.description
        )
        
        # 调用命令类自己的配置方法来定义参数
        command.configure_parser(cmd_parser)

    def run_interactive(self) -> int:
        """
        启动交互式 REPL (Read-Eval-Print Loop) 模式。
        允许用户在不重启程序的情况下连续执行多条命令。
        """
        print(f"欢迎使用 {self.prog_name} 交互模式。")
        print("输入 'help' 或 '?' 查看命令列表。输入 'exit' 退出程序。")

        # 尝试导入 prompt_toolkit 以实现高级补全功能
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.styles import Style
            from prompt_toolkit.document import Document
            
            class CLICompleter(Completer):
                """
                自定义补全器，支持命令和参数的上下文感知补全。
                """
                def __init__(self, app):
                    self.app = app
                    self.command_options = {}
                    self._init_options()
                
                def _init_options(self):
                    # 提取每个子命令的参数选项
                    if self.app.subparsers and hasattr(self.app.subparsers, 'choices'):
                        for cmd_name, parser in self.app.subparsers.choices.items():
                            opts = []
                            for action in parser._actions:
                                opts.extend(action.option_strings)
                            self.command_options[cmd_name] = opts
                    
                    # 添加内置命令（无参数）
                    for cmd in ['exit', 'help', '?']:
                        if cmd not in self.command_options:
                            self.command_options[cmd] = []

                def get_completions(self, document: Document, complete_event):
                    # 获取光标前的文本并去除左侧空白
                    text = document.text_before_cursor.lstrip()
                    
                    # 情况 1: 正在输入第一个词（命令名）
                    if ' ' not in text:
                        for cmd in self.command_options.keys():
                            if cmd.startswith(text):
                                yield Completion(cmd, start_position=-len(text))
                        return

                    # 情况 2: 已经输入了命令，正在输入参数
                    # 获取第一个词作为命令
                    first_word = text.split()[0]
                    
                    if first_word in self.command_options:
                        # 获取光标前的单词（用于匹配参数前缀）
                        word_before_cursor = document.get_word_before_cursor(WORD=True)
                        
                        # 获取该命令的所有可用参数
                        options = self.command_options[first_word]
                        
                        # 简单的优化：如果已经输入了某个参数，理论上不应该再次提示它
                        # 但为了简单起见，我们这里总是提示所有匹配前缀的参数
                        for opt in options:
                            if opt.startswith(word_before_cursor):
                                yield Completion(opt, start_position=-len(word_before_cursor))

            # 初始化自定义补全器
            completer = CLICompleter(self)
            
            # 定义简单的样式
            style = Style.from_dict({
                'prompt': '#ansigreen bold',
            })
            
            session = PromptSession(completer=completer, style=style)
            use_prompt_toolkit = True
        except ImportError:
            print("警告: 未安装 prompt_toolkit。自动补全已禁用。", file=sys.stderr)
            use_prompt_toolkit = False
        
        while True:
            try:
                # 获取用户输入
                if use_prompt_toolkit:
                    user_input = session.prompt(f"{self.prog_name}> ")
                else:
                    user_input = input(f"{self.prog_name}> ")
                
                user_input = user_input.strip()
                if not user_input:
                    continue
                
                # 内置的退出逻辑
                if user_input.lower() in ('exit'):
                    break
                
                # 内置的全局帮助逻辑
                if user_input.lower() in ('help', '?'):
                    self.parser.print_help()
                    continue

                # 使用 shlex.split 模拟 shell 的参数分割规则，支持引号包含的参数
                # 在 Windows 上 shlex 默认按 POSIX 规则处理（反斜杠是转义符），导致路径 E:\path 被错误解析
                # 因此需指定 posix=False 以支持 Windows 风格路径（反斜杠仅作为分隔符）
                # 注意：posix=False 会保留引号，所以我们需要手动处理引号去除
                is_windows = sys.platform.startswith('win')
                argv = shlex.split(user_input, posix=not is_windows)
                
                # 如果是 Windows 模式，shlex 不会自动去除引号，我们需要手动去除
                if is_windows:
                    argv = [arg.strip('"\'') for arg in argv]
                
                try:
                    # 在当前进程中执行解析和运行逻辑
                    self.run(argv)
                except ArgumentParserError as e:
                    # 捕获解析错误，打印后继续循环
                    print(f"错误: {e}")
                except SystemExit:
                    # 捕获 argparse 帮助信息可能触发的退出
                    pass
                    
            except KeyboardInterrupt:
                # 处理 Ctrl+C，不退出程序，仅提示如何退出
                print("\n输入 'exit' 退出程序。")
            except EOFError:
                # 处理 Ctrl+D (Linux) 或 Ctrl+Z (Windows) 结束输入
                print("\n正在退出...")
                break
            except Exception as e:
                # 捕获并显示执行过程中的意外错误
                print(f"意外错误: {e}")
        
        return 0

    def run(self, argv: Optional[List[str]] = None) -> int:
        """
        解析并运行命令。
        
        Args:
            argv: 参数列表。如果为 None，则使用命令行传入的参数。
                  如果最终 argv 为空，则进入交互模式。
            
        Returns:
            退出码 (0 为成功)。
        """
        if argv is None:
            argv = sys.argv[1:]
            
        # 智能切换：如果没有命令行参数，自动开启交互模式
        if not argv:
            return self.run_interactive()

        try:
            # 解析参数
            args = self.parser.parse_args(argv)
            if not args.command:
                self.parser.print_help()
                return 0
                
            # 执行找到的命令
            command = self._commands[args.command]
            return command.execute(args)
            
        except ArgumentParserError as e:
            # 在非交互模式下，如果是解析错误，返回 1
            msg = translate_error(str(e))
            print(f"用法错误: {msg}", file=sys.stderr)
            return 1
        except Exception as e:
            # 处理业务逻辑执行中的异常
            print(f"错误: {e}", file=sys.stderr)
            return 1
