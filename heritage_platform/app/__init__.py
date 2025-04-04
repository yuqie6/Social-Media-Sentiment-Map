from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from config import config
from flask_wtf.csrf import CSRFProtect
from flask_migrate import Migrate
from flask_socketio import SocketIO
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_cors import CORS
from .utils.logging_config import setup_logging, log_access
from .utils.security_config import setup_security
import os
import markdown

# 初始化扩展模块
# db: SQLAlchemy数据库ORM对象，用于处理所有数据库操作和模型定义
db = SQLAlchemy()
# login_manager: Flask-Login扩展，管理用户会话和身份验证
login_manager = LoginManager()
# csrf: Flask-WTF CSRF保护扩展，防止跨站请求伪造攻击
csrf = CSRFProtect()
# migrate: Flask-Migrate扩展，处理数据库迁移
migrate = Migrate()
# socketio: Flask-SocketIO扩展，提供WebSocket支持，实现实时通信功能
socketio = SocketIO(ping_timeout=20, ping_interval=10)  # 初始化SocketIO，设置心跳检测参数
# limiter: Flask-Limiter扩展，实现API请求速率限制，防止滥用
limiter = Limiter(key_func=get_remote_address)  # 初始化速率限制器，基于请求IP地址进行限制
# cors: Flask-CORS扩展, 处理跨域资源共享
cors = CORS()

# 配置登录管理器
login_manager.login_view = 'auth.login'  # 设置未登录用户重定向的目标视图
login_manager.login_message = '请先登录再访问此页面'  # 设置登录提示消息
login_manager.login_message_category = 'info'  # 设置消息类别为info类型

def create_app(config_name='default'):
    """创建并配置Flask应用实例
    
    此函数实现了应用工厂模式，根据配置名称创建相应环境的Flask应用
    
    Args:
        config_name (str): 配置名称，对应config.py中的配置类，可选值包括'development', 'production', 'default'
        
    Returns:
        Flask: 配置完成的Flask应用实例
    """
    # 创建Flask应用实例
    app = Flask(__name__)
    # 从配置对象加载应用配置
    app.config.from_object(config[config_name])
    # 调用配置对象的初始化方法，进行额外配置
    config[config_name].init_app(app)
    
    # 初始化日志系统，配置日志记录器和处理器
    setup_logging(app)
    
    # 初始化安全配置，设置安全相关的HTTP头部
    setup_security(app)
    
    # 注册访问日志中间件，记录每个请求的详细信息
    app.after_request(log_access)
    
    # 初始化各种Flask扩展
    db.init_app(app)  # 初始化数据库
    login_manager.init_app(app)  # 初始化登录管理器
    csrf.init_app(app)  # 初始化CSRF保护
    migrate.init_app(app, db)  # 初始化数据库迁移
    # 初始化CORS
    cors.init_app(app, resources={r"/*": {"origins": "*"}})
    # 初始化SocketIO并配置CORS，允许特定域名的跨域请求
    socketio.init_app(
        app, 
        cors_allowed_origins="*", 
        ping_timeout=20, 
        ping_interval=10,
        async_mode='eventlet',
        logger=True,
        engineio_logger=True
    )
    limiter.init_app(app)  # 初始化请求速率限制器
    
    # 初始化WebSocket管理器，处理WebSocket连接和事件
    from app.utils.websocket_manager import init_websocket_manager
    init_websocket_manager(app)
    
    # 确保日志目录存在，防止应用运行时因目录不存在而崩溃
    os.makedirs('logs', exist_ok=True)
    
    # 注册API蓝图（前后端分离的API接口）
    from app.api import api_bp
    csrf.exempt(api_bp)  # 豁免API路由的CSRF保护，便于前端框架和移动应用调用
    app.register_blueprint(api_bp, url_prefix='/api')
    
    # 注册视图蓝图
    # 这些蓝图划分了应用的不同功能模块
    from app.routes.main import main_bp  # 主页和基本页面
    from app.routes.auth import auth_bp  # 认证相关（登录、注册等）
    from app.routes.heritage import heritage_bp  # 非遗项目管理
    from app.routes.content import content_bp  # 内容管理（文章、视频等）
    from app.routes.forum import forum_bp  # 论坛讨论功能
    from app.routes.user import user_bp  # 用户管理
    from app.routes.notification import bp as notification_bp  # 通知系统
    from app.routes.message import bp as message_bp  # 消息系统
    
    # 将各个蓝图注册到应用实例
    app.register_blueprint(main_bp)
    app.register_blueprint(auth_bp, url_prefix='/auth')
    app.register_blueprint(heritage_bp, url_prefix='/heritage')
    app.register_blueprint(content_bp, url_prefix='/content')
    app.register_blueprint(forum_bp, url_prefix='/forum')
    app.register_blueprint(user_bp, url_prefix='/user')
    app.register_blueprint(notification_bp, url_prefix='/notification')
    app.register_blueprint(message_bp, url_prefix='/message')
    
    # 注册全局错误处理器，统一处理不同类型的HTTP错误
    from app.routes import errors
    app.register_error_handler(404, errors.page_not_found)  # 页面未找到
    app.register_error_handler(500, errors.internal_server_error)  # 服务器内部错误
    app.register_error_handler(403, errors.forbidden)  # 权限不足
    
    # 注册上下文处理器，为所有模板提供通用数据
    from app.utils.context_processors import common_data
    app.context_processor(common_data)
    
    # 添加模板过滤器 - Markdown渲染
    @app.template_filter('markdown')
    def render_markdown(content):
        """将Markdown格式文本转换为HTML
        
        此过滤器用于在模板中直接渲染Markdown内容，如文章、评论等
        
        Args:
            content (str): Markdown格式的文本内容
            
        Returns:
            str: 转换后的HTML内容
        """
        if content:
            return markdown.markdown(content, extensions=[
                'markdown.extensions.fenced_code',  # 支持代码块语法
                'markdown.extensions.tables',       # 支持表格语法
                'markdown.extensions.nl2br',        # 自动将换行转为<br>标签
                'markdown.extensions.extra'         # 额外功能扩展集合
            ])
        return ''
    
    # 添加nl2br过滤器，用于私信等简单文本内容显示
    @app.template_filter('nl2br')
    def nl2br(value):
        """将文本中的换行符转换为HTML的<br>标签
        
        此过滤器用于简单文本的显示，不需要完整Markdown支持的场景
        
        Args:
            value (str): 包含换行符的文本
            
        Returns:
            str: 转换后的HTML内容，换行符被替换为<br>标签
        """
        if value:
            return value.replace('\n', '<br>')
        return ''
    
    # 导入Socket.IO事件处理模块，设置WebSocket事件监听
    with app.app_context():
        from app import socket_events
    
    # 返回配置完成的应用实例
    return app
