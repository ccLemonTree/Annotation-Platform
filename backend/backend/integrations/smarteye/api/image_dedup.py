# -*- coding: utf-8 -*-
"""
图片相似度去重模块
基于感知哈希算法检测和移除重复图片
"""
import os
import shutil
import imagehash
from PIL import Image
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict
from itertools import combinations
from integrations.smarteye.api.utils import logger


class ImageDeduplicator:
    """图片去重器"""

    def __init__(self, threshold: int = 10):
        """
        Args:
            threshold: 相似度阈值，0=完全相同，数值越大相似度越低，推荐 0~10
        """
        self.threshold = threshold
        self.hash_cache: Dict[str, imagehash.ImageHash] = {}
        self.duplicate_groups: List[List[str]] = []
        self.comparison_count = 0

    def compute_hash(self, img_path: str) -> Optional[imagehash.ImageHash]:
        """
        计算图片的感知哈希值

        Args:
            img_path: 图片路径

        Returns:
            ImageHash 对象，失败返回 None
        """
        if img_path in self.hash_cache:
            return self.hash_cache[img_path]

        try:
            with Image.open(img_path) as img:
                # 统一转灰度图、缩放，提升比对稳定性
                img = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
                img_hash = imagehash.dhash(img)
                self.hash_cache[img_path] = img_hash
                return img_hash
        except Exception as e:
            logger.warning(f"[去重] 计算哈希失败: {img_path} - {e}")
            return None

    def find_duplicates(
        self,
        image_paths: List[str],
        progress_callback=None
    ) -> Tuple[List[List[str]], int]:
        """
        查找重复图片分组

        Args:
            image_paths: 图片路径列表
            progress_callback: 进度回调函数 (current, total)

        Returns:
            (分组列表, 唯一图片数)
            每个分组包含相似的图片路径，第一个是保留的，后续是重复的
        """
        logger.info(f"[去重] 开始计算 {len(image_paths)} 张图片的哈希...")

        # 1. 计算所有图片的哈希
        img_hash_list = []
        total_work = max(len(image_paths) * 2, 1)
        for idx, path in enumerate(image_paths):
            if progress_callback and idx % 100 == 0:
                progress_callback(idx, total_work)

            img_hash = self.compute_hash(path)
            if img_hash:
                img_hash_list.append((path, img_hash))

        logger.info(f"[去重] 有效图片: {len(img_hash_list)}/{len(image_paths)}")

        # 2. 相似度分组
        groups = []
        representative_hashes = []
        block_indexes = [defaultdict(list) for _ in range(4)]
        threshold = max(0, min(int(self.threshold), 10))
        block_radius = threshold // 4
        neighbor_masks = [0]
        for bit_count in range(1, block_radius + 1):
            for bits in combinations(range(16), bit_count):
                mask = 0
                for bit in bits:
                    mask |= 1 << bit
                neighbor_masks.append(mask)
        self.comparison_count = 0

        logger.info(f"[去重] 开始相似度比对...")
        for idx, (path1, hash1) in enumerate(img_hash_list):
            hash_value = int(str(hash1), 16)
            candidates = set()
            for block_idx, block_index in enumerate(block_indexes):
                block_value = (hash_value >> (block_idx * 16)) & 0xFFFF
                for mask in neighbor_masks:
                    candidates.update(block_index.get(block_value ^ mask, ()))

            matched_groups = []
            for group_idx in candidates:
                self.comparison_count += 1
                if (hash_value ^ representative_hashes[group_idx]).bit_count() <= threshold:
                    matched_groups.append(group_idx)

            if matched_groups:
                groups[min(matched_groups)].append(path1)
            else:
                group_idx = len(groups)
                groups.append([path1])
                representative_hashes.append(hash_value)
                for block_idx, block_index in enumerate(block_indexes):
                    block_value = (hash_value >> (block_idx * 16)) & 0xFFFF
                    block_index[block_value].append(group_idx)

            if progress_callback and idx % 100 == 0:
                progress_callback(len(image_paths) + idx, total_work)

        if progress_callback:
            progress_callback(total_work, total_work)

        # 3. 统计
        unique_count = len(groups)
        duplicate_count = sum(len(g) - 1 for g in groups if len(g) > 1)

        logger.info(f"[去重] 完成: 唯一={unique_count}, 重复={duplicate_count}, 比较={self.comparison_count}")

        self.duplicate_groups = groups
        return groups, unique_count

    def remove_duplicates(
        self,
        groups: List[List[str]],
        duplicate_folder: Optional[str] = None,
        delete: bool = False
    ) -> Dict[str, int]:
        """
        移除或移动重复图片

        Args:
            groups: 分组列表（来自 find_duplicates）
            duplicate_folder: 重复图片存放目录，None 则直接删除
            delete: True=直接删除，False=移动到 duplicate_folder

        Returns:
            统计信息 {"kept": 保留数, "moved": 移动数, "deleted": 删除数, "errors": 失败数}
        """
        stats = {"kept": 0, "moved": 0, "deleted": 0, "errors": 0}

        if duplicate_folder and not delete:
            os.makedirs(duplicate_folder, exist_ok=True)

        for group in groups:
            if len(group) == 1:
                stats["kept"] += 1
                continue

            # 保留第一个
            keep_file = group[0]
            dup_files = group[1:]
            stats["kept"] += 1

            # 处理重复文件
            for dup_path in dup_files:
                try:
                    if delete:
                        os.remove(dup_path)
                        stats["deleted"] += 1
                    else:
                        # 移动到重复文件夹
                        base_name = os.path.basename(dup_path)
                        target_path = os.path.join(duplicate_folder, base_name)

                        # 处理同名冲突
                        name, ext = os.path.splitext(base_name)
                        num = 1
                        while os.path.exists(target_path):
                            target_path = os.path.join(duplicate_folder, f"{name}_{num}{ext}")
                            num += 1

                        shutil.move(dup_path, target_path)
                        stats["moved"] += 1

                except Exception as e:
                    logger.warning(f"[去重] 处理失败: {dup_path} - {e}")
                    stats["errors"] += 1

        return stats

    def deduplicate_directory(
        self,
        source_dir: str,
        duplicate_folder: Optional[str] = None,
        extensions: Tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".gif"),
        delete: bool = False,
        progress_callback=None
    ) -> Dict[str, int]:
        """
        对整个目录进行去重

        Args:
            source_dir: 源目录
            duplicate_folder: 重复图片存放目录（None 则自动创建 source_dir/重复图片）
            extensions: 图片后缀白名单
            delete: True=直接删除重复图片，False=移动到 duplicate_folder
            progress_callback: 进度回调

        Returns:
            统计信息
        """
        if duplicate_folder is None:
            duplicate_folder = os.path.join(source_dir, "重复图片")

        # 1. 扫描所有图片
        logger.info(f"[去重] 扫描目录: {source_dir}")
        all_images = []
        for root, _, files in os.walk(source_dir):
            # 跳过重复文件夹本身
            if root == duplicate_folder:
                continue
            for file in files:
                if file.lower().endswith(extensions):
                    all_images.append(os.path.join(root, file))

        if not all_images:
            logger.warning(f"[去重] 未找到任何图片")
            return {"kept": 0, "moved": 0, "deleted": 0, "errors": 0, "total": 0}

        logger.info(f"[去重] 找到 {len(all_images)} 张图片")

        # 2. 查找重复
        groups, unique_count = self.find_duplicates(all_images, progress_callback)

        # 3. 移除重复
        stats = self.remove_duplicates(groups, duplicate_folder, delete)
        stats["total"] = len(all_images)
        stats["unique"] = unique_count

        return stats


def deduplicate_images(
    image_paths: List[str],
    threshold: int = 10,
    inplace: bool = False
) -> Tuple[List[str], Dict]:
    """
    对图片列表进行去重（快捷函数）

    Args:
        image_paths: 图片路径列表
        threshold: 相似度阈值（0-10）
        inplace: True=删除重复文件，False=只返回去重后的列表

    Returns:
        (去重后的图片路径列表, 统计信息)
    """
    deduplicator = ImageDeduplicator(threshold=threshold)
    groups, unique_count = deduplicator.find_duplicates(image_paths)

    # 保留每组的第一个
    unique_paths = [group[0] for group in groups]

    stats = {
        "total": len(image_paths),
        "unique": unique_count,
        "duplicates": len(image_paths) - len(unique_paths),
    }

    # 如果需要原地删除
    if inplace:
        deleted = 0
        for group in groups:
            for dup_path in group[1:]:
                try:
                    os.remove(dup_path)
                    deleted += 1
                except Exception as e:
                    logger.warning(f"[去重] 删除失败: {dup_path} - {e}")
        stats["deleted"] = deleted

    return unique_paths, stats


def scan_and_deduplicate(
    root_dir: str,
    threshold: int = 10,
    delete: bool = False
) -> Dict[str, int]:
    """
    扫描并去重整个目录（快捷函数）

    Args:
        root_dir: 根目录
        threshold: 相似度阈值
        delete: True=删除重复图片，False=移动到"重复图片"文件夹

    Returns:
        统计信息
    """
    deduplicator = ImageDeduplicator(threshold=threshold)
    return deduplicator.deduplicate_directory(
        root_dir,
        delete=delete
    )
