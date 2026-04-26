<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use App\Blockchain\Blockchain;
use Illuminate\Support\Facades\DB;
use Carbon\Carbon;

class VoteController extends Controller
{
     public function type_election(){
       
    $election=DB::table('elections')->latest('id')->first();
   
    return response()->json($election->type);

    }
     public function date(){
      
          $now = Carbon::now(); 
          $election=DB::table('elections')->latest('id')->first();
          $start = Carbon::parse($election->date_start);
          $end = Carbon::parse($election->date_end);

          if ($now->between($start, $end)) {
            return response()->json(['message'=>'True']);
          } else {
            return response()->json(['message'=>'False']);
          }
   

    }
    public function vote(Request $request)
    {

        $request->validate([
            'voter_N_id' => 'required',
            'candidates' => 'required|array',
                    
        ]);
        $N_id = $request->voter_N_id;

        $city_id = DB::table('users')->where('national_id',$N_id)->value('cities_id');
        $city = DB::table('cities')->where('id',$city_id)->value('name');
        $gov_id = DB::table("cities")->where('id',$city_id)->value("governorates_id");
        $province = DB::table('governorates')->where('id',$gov_id)->value('name');
        
        $block = Blockchain::addVote(
            $request->voter_N_id,
            $request->candidates,
            $province,
            $city,
            $request->list
        );

        $id = DB::table('users')->where('national_id',$N_id)->value('id');
        $election_id = DB::table('elections')->max('id');

        $result =  DB::table('voting_verified')->insert([
                "votecheck" => 1,
                "users_id" => $id,
                "elections_id" => $election_id
            ]);

        return response()->json([
            'message' => 'Vote recorded in blockchain!',
            
        ]);
    }
     public function verify()
    {
        $status = Blockchain::verifyChain();

        return response()->json([
            'blockchain_valid' => $status
        ]);
    }

    // دالة لفرز الأصوات حسب المحافظة لكل مرشح

    public function results(Request $request){
        $blocks = \App\Blockchain\Blockchain::getAllBlocks();
        $results_ind = [];
        $results_list = [];
        $totalVotes = 0;
        $totalListVotes = 0;

        $election_type = $request['election_type'];
        $N_id = $request['N_id'];

        $city_id = DB::table('users')->where('national_id',$N_id)->value('cities_id');
        $house_id = DB::table('cities')->where('id',$city_id)->value('house_id');
        $cities_house = DB::table('cities')->where('house_id',$house_id)->pluck('id');
        $cities_house_name = DB::table('cities')->whereIn('id',$cities_house)->pluck('name')->toArray();
        $gov_id = DB::table('cities')->where('id',$city_id)->value('governorates_id');
        $cities_govern = DB::table('cities')->where('governorates_id',$gov_id)->pluck('id');
        $cities_govern_name = DB::table('cities')->whereIn('id',$cities_govern)->pluck('name')->toArray();

        $list_dis = DB::table('governorates')->where('id',$gov_id)->value('lists_Dis_id');

        $gov_name = DB::table("governorates")->where("lists_Dis_id",$list_dis)->pluck('name')->toArray();

            if ($election_type == 'انتخابات مجلس النواب') {

                foreach ($blocks as $block) {
                    // فردي
                    if (in_array($block['city'], $cities_house_name)) {
                        foreach ($block['candidates'] as $candidate) {

                            if (!isset($results_ind[$candidate])) {
                                $results_ind[$candidate] = 0;
                            }

                            $results_ind[$candidate]++;
                            
                        }
                    }
                    // قوائم
                    if (in_array($block['province'], $gov_name)){
                        foreach ($block['list'] as $list) {

                            if (!isset($results_list[$list])) {
                                $results_list[$list] = 0;
                            }

                            $results_list[$list]++;
                            $totalListVotes++;
                        }

                    }
                    $totalVotes++;
                    
                }
                $final_individual = [];

                foreach ($results_ind as $id => $count) {

                    $user_id =  DB::table('candidates')->where('id', $id)->value('users_id');
                    $img = DB::table('candidates')->where('id', $id)->value('img');
                    $img = "uploads/". $img;
                    $img = asset($img);
                    $final_individual[] = [
                        'id' => $id,
                        "name" => DB::table('users')->where('id',$user_id)->value('full_name'),
                        "img" => $img,
                        'votes' => $count,
                        'percentage' => $totalVotes > 0 
                            ? round(($count / $totalVotes) * 100, 2)
                            : 0
                    ];
                }

                $final_list = [];

                foreach ($results_list as $id => $count) {
                    $img = DB::table('lists')->where('id',$id)->value('symbol_img');
                    $img = "uploads/". $img;
                    $img = asset($img);
                    $final_list[] = [
                        'id' => $id,
                        "name" => DB::table('lists')->where('id',$id)->value('name'),
                        "img" => $img,
                        'votes' => $count,
                        'percentage' => $totalListVotes > 0 
                            ? round(($count / $totalListVotes) * 100, 2)
                         : 0
                    ];
                }
                return response()->json([
                    'state' => 'success',
                    'totalvotes' => $totalVotes,
                    'totallistvotes' => $totalListVotes,
                    'persent_ind' => $final_individual,
                    'persent_list' => $final_list
                ]);

              }elseif($election_type == 'انتخابات مجلس الشيوخ'){
                foreach ($blocks as $block) {
                    // فردي
                    if (in_array($block['city'], $cities_govern_name)) {
                        foreach ($block['candidates'] as $candidate) {

                            if (!isset($results_ind[$candidate])) {
                                $results_ind[$candidate] = 0;
                            }

                            $results_ind[$candidate]++;
                            $totalVotes++;
                        }
                    }
                    // قوائم
                    if (in_array($block['province'], $gov_name)){
                        foreach ($block['list'] as $list) {

                            if (!isset($results_list[$list])) {
                                $results_list[$list] = 0;
                            }

                            $results_list[$list]++;
                            $totalListVotes++;
                        }

                    }
                    
                }
                $final_individual = [];

                foreach ($results_ind as $id => $count) {
                    $user_id =  DB::table('candidates')->where('id', $id)->value('users_id');
                    $img = DB::table('candidates')->where('id', $id)->value('img');
                    $img = "uploads/". $img;
                    $img = asset($img);

                    $final_individual[] = [
                        'id' => $id,
                        "name" => DB::table('users')->where('id',$user_id)->value('full_name'),
                        "img" => $img,
                        'votes' => $count,
                        'percentage' => $totalVotes > 0 
                            ? round(($count / $totalVotes) * 100, 2)
                            : 0
                    ];
                }

                $final_list = [];

                foreach ($results_list as $id => $count) {
                    $img = DB::table('lists')->where('id',$id)->value('symbol_img');
                    $img = "uploads/". $img;
                    $img = asset($img);
                    $final_list[] = [
                        'id' => $id,
                        "name" => DB::table('lists')->where('id',$id)->value('name'),
                        "img" => $img,
                        'votes' => $count,
                        'percentage' => $totalListVotes > 0 
                            ? round(($count / $totalListVotes) * 100, 2)
                         : 0
                    ];
                }
                return response()->json([
                    'state' => 'success',
                    'totalvotes' => $totalVotes,
                    'totallistvotes' => $totalListVotes,
                    'persent_ind' => $final_individual,
                    'persent_list' => $final_list
                ]);

              }elseif($election_type == 'الانتخابات الرئاسية'){

                foreach ($blocks as $block) {

                    foreach ($block['candidates'] as $candidate) {

                        if (!isset($results_ind[$candidate])) {
                            $results_ind[$candidate] = 0;
                        }

                        $results_ind[$candidate]++;
                        $totalVotes++;
                    }
                }
                $final_individual = [];

                foreach ($results_ind as $id => $count) {
                    $user_id =  DB::table('candidates')->where('id', $id)->value('users_id');
                    $img = DB::table('candidates')->where('id', $id)->value('img');
                    $img = "uploads/". $img;
                    $img = asset($img);
                    $final_individual[] = [
                        'id' => $id,
                        "name" => DB::table('users')->where('id',$user_id)->value('full_name'),
                        "img" => $img,
                        'votes' => $count,
                        'percentage' => $totalVotes > 0 
                            ? round(($count / $totalVotes) * 100, 2)
                            : 0
                    ];
                }

                return response()->json([
                    'state' => 'success',
                    'totalvotes' => $totalVotes,
                    'persent_ind' => $final_individual
                ]);
            }
            
    }
}
