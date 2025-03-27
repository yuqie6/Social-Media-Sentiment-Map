import os
import uuid
import traceback
from werkzeug.utils import secure_filename
from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app, jsonify
from flask_login import current_user, login_required
from app import db, csrf
from app.models import Content, HeritageItem, Comment, Like, Favorite
from app.forms.content import ContentForm, CommentForm
from app.utils.file_handlers import ALLOWED_IMAGE_EXTENSIONS, allowed_file, save_file
from sqlalchemy.exc import SQLAlchemyError

content_bp = Blueprint('content', __name__)

@content_bp.route('/list')
def list():
    """内容列表页"""
    page = request.args.get('page', 1, type=int)
    content_type = request.args.get('type')
    heritage_id = request.args.get('heritage_id', type=int)
    per_page = 12
    
    try:
        # 使用查询构建器并加载关联的用户信息
        query = Content.query.join(Content.author)
        
        if content_type:
            query = query.filter(Content.content_type == content_type)
            
        if heritage_id:
            query = query.filter(Content.heritage_id == heritage_id)
            
        # 应用排序并进行分页
        pagination = query.order_by(Content.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False)
        
        items = pagination.items
        
    except Exception as e:
        current_app.logger.error(f"内容列表查询错误: {str(e)}")
        # 降级处理：返回空列表
        items = []
        pagination = None
    
    # 获取所有非遗项目供筛选用
    try:
        heritage_items = HeritageItem.query.all()
    except Exception as e:
        current_app.logger.error(f"获取非遗项目列表错误: {str(e)}")
        heritage_items = []
    
    return render_template('content/list.html', 
                           items=items, 
                           pagination=pagination,
                           heritage_items=heritage_items,
                           current_type=content_type,
                           current_heritage_id=heritage_id)

@content_bp.route('/detail/<int:id>', methods=['GET', 'POST'])
def detail(id):
    """内容详情页"""
    content = Content.query.get_or_404(id)
    
    # 评论表单
    form = CommentForm()
    
    if form.validate_on_submit() and current_user.is_authenticated:
        try:
            comment = Comment(
                user_id=current_user.id,
                content_id=id,
                text=form.text.data
            )
            
            db.session.add(comment)
            db.session.commit()
            
            flash('评论发布成功', 'success')
            return redirect(url_for('content.detail', id=id))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"发布评论失败: {str(e)}")
            flash('发布评论失败，请稍后重试', 'danger')
    
    # 获取评论列表
    page = request.args.get('page', 1, type=int)
    comments_pagination = Comment.query.filter_by(content_id=id).order_by(
        Comment.created_at.desc()).paginate(
        page=page, per_page=10, error_out=False)
    
    # 检查当前用户是否已点赞
    has_liked = False
    if current_user.is_authenticated:
        has_liked = Like.query.filter_by(
            user_id=current_user.id, 
            content_id=id
        ).first() is not None
    
    # 检查当前用户是否已收藏
    has_favorited = False
    if current_user.is_authenticated:
        has_favorited = Favorite.query.filter_by(
            user_id=current_user.id, 
            content_id=id
        ).first() is not None
    
    return render_template('content/detail.html', 
                           content=content,
                           form=form,
                           comments=comments_pagination.items,
                           pagination=comments_pagination,
                           has_liked=has_liked,
                           has_favorited=has_favorited)

@content_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    """创建内容页面"""
    form = ContentForm()
    
    # 动态加载非遗项目选项
    form.heritage_id.choices = [(h.id, h.name) for h in HeritageItem.query.all()]
    
    if form.validate_on_submit():
        try:
            content = Content(
                title=form.title.data,
                heritage_id=form.heritage_id.data,
                user_id=current_user.id,
                content_type=form.content_type.data
            )
            
            # 根据内容类型处理不同字段
            if form.content_type.data == 'article':
                content.text_content = form.text_content.data
            elif form.content_type.data == 'multimedia':
                content.rich_content = form.rich_content.data
            elif form.content_type.data in ['image', 'video']:
                if form.file.data:
                    current_app.logger.info(f"处理文件上传: {form.file.data.filename}, 类型: {form.content_type.data}")
                    file_path = save_file(form.file.data, form.content_type.data)
                    if file_path:
                        current_app.logger.info(f"文件上传成功，路径: {file_path}")
                        content.file_path = file_path
                    else:
                        current_app.logger.error("文件上传失败")
                        flash('文件上传失败', 'danger')
                        return render_template('content/create.html', form=form)
                else:
                    current_app.logger.warning(f"未检测到文件上传")
            
            db.session.add(content)
            db.session.commit()
            current_app.logger.info(f"内容创建成功：ID={content.id}, 标题={content.title}")
            
            flash('内容创建成功', 'success')
            return redirect(url_for('content.detail', id=content.id))
            
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"创建内容失败: {str(e)}")
            current_app.logger.error(traceback.format_exc())
            flash('创建内容失败，请稍后重试', 'danger')
    
    return render_template('content/create.html', form=form)

# 添加图片上传API端点，并豁免CSRF保护
@content_bp.route('/upload_image', methods=['POST'])
@login_required
@csrf.exempt   # 添加CSRF豁免
def upload_image():
    """富文本编辑器的图片上传处理"""
    try:
        current_app.logger.info("收到文件上传请求")
        
        # 检查请求中是否包含文件
        if 'upload' not in request.files:
            current_app.logger.warning("请求中没有'upload'文件")
            return jsonify({
                'uploaded': 0,
                'error': {'message': '没有文件上传'}
            })
        
        file = request.files['upload']
        if file.filename == '':
            current_app.logger.warning("上传了空文件名")
            return jsonify({
                'uploaded': 0,
                'error': {'message': '未选择文件'}
            })
        
        # 记录请求信息用于调试
        current_app.logger.info(f"文件名: {file.filename}")
        current_app.logger.info(f"Content-Type: {file.content_type}")
        
        # 检查文件类型
        if not allowed_file(file.filename, ALLOWED_IMAGE_EXTENSIONS):
            current_app.logger.warning(f"不支持的文件类型: {file.filename}")
            return jsonify({
                'uploaded': 0,
                'error': {'message': '不支持的文件类型，请上传jpg, jpeg, png或gif格式'}
            })
        
        # 重置文件指针位置，确保能正确读取文件内容
        file.seek(0)
        
        try:
            # 创建上传目录
            upload_path = os.path.join(
                current_app.root_path, 
                'static', 'uploads', 'images'
            )
            current_app.logger.info(f"上传路径: {upload_path}")
            os.makedirs(upload_path, exist_ok=True)
            
            # 创建安全的文件名
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            file_path = os.path.join(upload_path, unique_filename)
            
            # 直接保存文件
            file.save(file_path)
            current_app.logger.info(f"文件保存到: {file_path}")
            
            # 构建URL (确保生成的URL以/static/开头)
            relative_path = f"uploads/images/{unique_filename}"
            url = url_for('static', filename=relative_path)
            
            current_app.logger.info(f"生成的URL: {url}")
            
            # 确认文件已成功保存
            if os.path.exists(file_path):
                current_app.logger.info(f"文件确认存在: {file_path}")
                # 返回CKEditor需要的格式
                return jsonify({
                    'uploaded': 1,
                    'fileName': filename,
                    'url': url
                })
            else:
                current_app.logger.error(f"文件保存后不存在: {file_path}")
                return jsonify({
                    'uploaded': 0, 
                    'error': {'message': '文件保存失败，请稍后重试'}
                })
                
        except Exception as e:
            current_app.logger.error(f"处理上传图片时出错: {str(e)}")
            current_app.logger.error(traceback.format_exc())
            return jsonify({
                'uploaded': 0,
                'error': {'message': f'上传处理失败: {str(e)}'}
            })
    
    except Exception as e:
        current_app.logger.error(f"图片上传过程中发生未处理的错误: {str(e)}")
        current_app.logger.error(traceback.format_exc())
        return jsonify({
            'uploaded': 0,
            'error': {'message': f'服务器错误: {str(e)}'}
        })

@content_bp.route('/like/<int:id>', methods=['POST'])
@login_required
def like(id):
    """点赞内容"""
    content = Content.query.get_or_404(id)
    
    # 检查是否已点赞
    existing_like = Like.query.filter_by(
        user_id=current_user.id,
        content_id=id
    ).first()
    
    try:
        if existing_like:
            # 取消点赞
            db.session.delete(existing_like)
            message = '取消点赞成功'
        else:
            # 添加点赞
            like = Like(user_id=current_user.id, content_id=id)
            db.session.add(like)
            message = '点赞成功'
            
        db.session.commit()
        flash(message, 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"点赞操作失败: {str(e)}")
        flash('操作失败，请稍后重试', 'danger')
        
    return redirect(url_for('content.detail', id=id))

@content_bp.route('/favorite/<int:id>', methods=['POST'])
@login_required
def favorite(id):
    """收藏内容"""
    content = Content.query.get_or_404(id)
    
    # 检查是否已收藏
    existing_favorite = Favorite.query.filter_by(
        user_id=current_user.id,
        content_id=id
    ).first()
    
    try:
        if existing_favorite:
            # 取消收藏
            db.session.delete(existing_favorite)
            message = '取消收藏成功'
        else:
            # 添加收藏
            favorite = Favorite(user_id=current_user.id, content_id=id)
            db.session.add(favorite)
            message = '收藏成功'
            
        db.session.commit()
        flash(message, 'success')
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"收藏操作失败: {str(e)}")
        flash('操作失败，请稍后重试', 'danger')
        
    return redirect(url_for('content.detail', id=id))
