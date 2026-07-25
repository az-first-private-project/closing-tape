"""
SQLAlchemy 声明性基类
-------------------
所有 ORM 模型都继承自这个 Base 类。
SQLAlchemy 通过它来追踪所有模型定义，并生成 DDL 语句。
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """
    SQLAlchemy 2.0 风格的声明性基类

    继承这个类的子类会自动被 SQLAlchemy 的 metadata 追踪，
    方便后续使用 Base.metadata.create_all() 创建所有表。
    """
    pass