<?php

namespace App\Http\Controllers;

use Illuminate\Http\Request;
use Illuminate\Support\Facades\DB;
use Spatie\PdfToText\Pdf;

class CandidatesController extends Controller
{
    
    public function candidates(Request $request){
        $request->validate([
            'election_type' => 'required',
            'list_type' => 'required',
            'N_id' => 'required'          
        ]);

        $election_type = $request['election_type'];
        $list_type = $request['list_type'];
        $N_id = $request['N_id'];
        $id = DB::table('users')->where('national_id',$N_id)->value('id');

        if(!$id){
            return response()->json(["error" => 'User Is Not Found']);
        }

        $city_id = DB::table('users')->where('national_id',$N_id)->value('cities_id');
        $house_id = DB::table('cities')->where('id',$city_id)->value('house_id');
        $cities_hosue = DB::table('cities')->where('house_id',$house_id)->pluck('id');
        $gov_id = DB::table('cities')->where('id',$city_id)->value('governorates_id');
        $cities_govern = DB::table('cities')->where('governorates_id',$gov_id)->pluck('id');
        $list_dis = DB::table('governorates')->where('id',$gov_id)->value('lists_Dis_id');
        $list_name = DB::table('lists')->select('id','name','symbol','symbol_img','program')->where('lists_Dis_id',$list_dis)->get();
        foreach ($list_name as $item) {
                        $item->symbol_img = "uploads/". $item->symbol_img;
                        $item->symbol_img = asset($item->symbol_img);

                        $file = $item->program;
                        if ($file) {
                            $path = public_path('uploads/' . $file);

                            if (file_exists($path)) {
                                try {
                                    $text = Pdf::getText($path, "C:\poppler\Library\bin\pdftotext.exe");
                                    $item->program = $text;
                                } catch (\Exception $e) {
                                    $item->program = "error reading pdf";
                                }
                            } else {
                                $item->program = "file not exist";
                            }
                        } else {
                            $item->program = "no file";
                        }
                    }

        if($election_type == "انتخابات مجلس النواب"){
            if($list_type == "فردى"){
                $candidates = DB::table('users')
                    ->join('candidates','users.id','=','candidates.users_id')
                    ->select(
                        'candidates.id',
                        'users.full_name',
                        'candidates.job',
                        'candidates.description',
                        'candidates.img',
                        'candidates.symbol',
                        'candidates.symbol_img'
                    )
                    ->whereIn('users.cities_id',$cities_hosue)
                    ->where('users.role','candidate')
                    ->where('candidates.type','فردى')
                    ->get();
                    foreach ($candidates as $item) {
                        $item->img = "uploads/". $item->img;
                        $item->symbol_img = "uploads/". $item->symbol_img;
                        $item->img = asset($item->img);
                        $item->symbol_img = asset($item->symbol_img);

                        $file = $item->description;
                        if ($file) {
                            $path = public_path('uploads/' . $file);

                            if (file_exists($path)) {
                                try {
                                    $text = Pdf::getText($path, "C:\poppler\Library\bin\pdftotext.exe");
                                    $item->description = $text;
                                } catch (\Exception $e) {
                                    $item->description = "error reading pdf";
                                }
                            } else {
                                $item->description = "file not exist";
                            }
                        } else {
                            $item->description = "no file";
                        }

                    }
                 return response()->json($candidates);
            }elseif($list_type == "قوائم"){
                
                return response()->json($list_name);
            }
        }elseif($election_type == 'انتخابات مجلس الشيوخ'){
            if($list_type == "فردى"){
                $candidates = DB::table('users')
                    ->join('candidates','users.id','=','candidates.users_id')
                    ->select(
                        'candidates.id',
                        'users.full_name',
                        'candidates.job',
                        'candidates.description',
                        'candidates.img',
                        'candidates.symbol',
                        'candidates.symbol_img'
                    )
                    ->whereIn('users.cities_id',$cities_govern)
                    ->where('users.role','candidate')
                    ->where('candidates.type','فردى')
                    ->get();
                    foreach ($candidates as $item) {
                        $item->img = "uploads/". $item->img;
                        $item->symbol_img = "uploads/". $item->symbol_img;
                        $item->img = asset($item->img);
                        $item->symbol_img = asset($item->symbol_img);

                        $file = $item->description;
                        if ($file) {
                            $path = public_path('uploads/' . $file);

                            if (file_exists($path)) {
                                try {
                                    $text = Pdf::getText($path, "C:\poppler\Library\bin\pdftotext.exe");
                                    $item->description = $text;
                                } catch (\Exception $e) {
                                    $item->description = "error reading pdf";
                                }
                            } else {
                                $item->description = "file not exist";
                            }
                        } else {
                            $item->description = "no file";
                        }
                    }
                return response()->json($candidates);
            }elseif($list_type == "قوائم"){
                return response()->json($list_name);
            }
        }elseif($election_type == 'الانتخابات الرئاسية'){
            $candidates = DB::table('users')
                    ->join('candidates','users.id','=','candidates.users_id')
                    ->select(
                        'candidates.id',
                        'users.full_name',
                        'candidates.job',
                        'candidates.description',
                        'candidates.img',
                        'candidates.symbol',
                        'candidates.symbol_img'
                    )
                    ->where('users.role','candidate')
                    ->where('candidates.type','فردى')
                    ->get();
                    foreach ($candidates as $item) {
                        $item->img = "uploads/". $item->img;
                        $item->symbol_img = "uploads/". $item->symbol_img;
                        $item->img = asset($item->img);
                        $item->symbol_img = asset($item->symbol_img);

                        $file = $item->description;
                        if ($file) {
                            $path = public_path('uploads/' . $file);

                            if (file_exists($path)) {
                                try {
                                    $text = Pdf::getText($path, "C:\poppler\Library\bin\pdftotext.exe");
                                    $item->description = $text;
                                } catch (\Exception $e) {
                                    $item->description = "error reading pdf";
                                }
                            } else {
                                $item->description = "file not exist";
                            }
                        } else {
                            $item->description = "no file";
                        }
                       

                        
                    }
                return response()->json($candidates);
        }



    }

    public function one(Request $request){
        $id = $request["id"];
        if(!$id){
            return response()->json(['state' => "Candidate Is Not Found"]);
        }
        $candidates = DB::table('users')
        ->join('candidates','users.id','=','candidates.users_id')
        ->select(
            'candidates.id',
            'users.full_name',
            'candidates.job',
            'candidates.description',
            'candidates.img',
            'candidates.symbol',
            'candidates.symbol_img'
        )->where('users.id',$id)->get();
        foreach ($candidates as $item) {
                        $item->img = "uploads/". $item->img;
                        $item->symbol_img = "uploads/". $item->symbol_img;
                        $item->img = asset($item->img);
                        $item->symbol_img = asset($item->symbol_img);

                        $file = $item->description;
                        if ($file) {
                            $path = public_path('uploads/' . $file);

                            if (file_exists($path)) {
                                try {
                                    $text = Pdf::getText($path, "C:\poppler\Library\bin\pdftotext.exe");
                                    $item->description = $text;
                                } catch (\Exception $e) {
                                    $item->description = "error reading pdf";
                                }
                            } else {
                                $item->description = "file not exist";
                            }
                        } else {
                            $item->description = "no file";
                        }
                       
                    }
        return response()->json($candidates);

    }
    

    public function candidates_list(Request $request){
        $id = $request['id'];
        if(!$id){
            return response()->json(['state' => "List Is Not Found"]);
        }
        $candidates = DB::table('users')->join('candidates','users.id','=','candidates.users_id')
                                        ->select('candidates.id','users.full_name')->where('candidates.type','قوائم')->where('candidates.lists_id',$id)->get();
        
          return response()->json($candidates);                            
    }

    public function ins_list(){
        $last_id = DB::table('elections')->max('id');
        $instructure = DB::table('elections')->where('id',$last_id)->value('inst_list');
        return response()->json($instructure);
    }

    public function ins_indiv(){
        $last_id = DB::table('elections')->max('id');
        $instructure = DB::table('elections')->where('id',$last_id)->value('inst_indiv');
        return response()->json($instructure);
    }

    public function voteCheck(Request $request){
        $N_id = $request['N_id'];
        $name = $request['name'];

        $id = DB::table('users')->where('national_id',$N_id)->value('id');

        if(!$id){
            return response()->json(["error" => 'User is not Found']);
        }

        $name_DB = DB::table('users')->where('national_id',$N_id)->value('full_name');

        if($name != $name_DB){
            return response()->json(["error" => "The name is not match"]);
        }
        
        
        $election_id = DB::table('elections')->max('id');
        $vote = DB::table('voting_verified')->where('users_id',$id)->where('elections_id',$election_id)->value('votecheck');

        if($vote){
            return response()->json([
                "states" => false,
                "message" => "The user is already voted"
            ]);
        }else{
            return response()->json([
                "states" => true,
            ]);
        }

    
    }

}



















 