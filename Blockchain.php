<?php

namespace App\Blockchain;

use Illuminate\Support\Facades\Storage;

class Blockchain
{
    // نحسب هاش البلوك
    public static function calculateHash($block)
    {
        return hash('sha256', $block['voter_N_id'] . json_encode($block['candidates']) . $block['province'] . $block['city'] . json_encode($block['list']) . $block['timestamp'] . ($block['previous_hash'] ?? ''));
    }

    // نضيف بلوك جديد
    public static function addVote($voter_N_id, $candidates, $province, $city, $list)
    {
        $blocks = self::getAllBlocks();
        $previousHash = count($blocks) ? end($blocks)['hash'] : null;

        $block = [
            'voter_N_id' => $voter_N_id,
            'candidates' => $candidates,
            'province' => $province,
            'city' => $city,
            'list' => $list,
            'timestamp' => time(),      
            'previous_hash' => $previousHash,
        ];

        $block['hash'] = self::calculateHash($block);

        // نحفظ البلوك
        $filename = storage_path('app/blockchain/' . $block['timestamp'] . '_' . uniqid() . '.json');
        file_put_contents($filename, json_encode($block, JSON_PRETTY_PRINT));

        return $block;
    }

    // نجيب كل البلوكات
    public static function getAllBlocks()
    {
        $blocks = [];
        $files = glob(storage_path('app/blockchain/*.json'));

        foreach ($files as $file) {
            $blocks[] = json_decode(file_get_contents($file), true);
        }

        // ✅ ترتيب حسب الوقت
        usort($blocks, fn($a, $b) => $a['timestamp'] <=> $b['timestamp']);

        return $blocks;
    }

    // نتحقق من صحة البلوك تشاين
    public static function verifyChain()
    {
        $blocks = self::getAllBlocks();
        foreach ($blocks as $index => $block) {
            if ($index == 0) continue; // البلوك الأول
            $prev = $blocks[$index - 1];

            if ($block['previous_hash'] !== $prev['hash']) {
                return false;
            }
            if ($block['hash'] !== self::calculateHash($block)) {
                return false;
            }
        }
        return true;
    }
}